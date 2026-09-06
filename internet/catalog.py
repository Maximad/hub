"""Bridge InternetPackage policy into Hub commercial/order infrastructure."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from catalog.models import MenuSection
from core.models import (
    ActivityLog,
    Category,
    InternetEntitlement,
    InternetNetworkOperation,
    InternetPackage,
    InternetSession,
    Order,
    Product,
)
from core.services.internet_access import create_entitlement, start_usage_session
from core.services.network_operations import enqueue_network_operation
from core.services.visit_internet import package_customer_error, self_service_enabled
from core.settings_helpers import get_system_settings
from members.benefits import resolve_internet_price
from .models import InternetCatalogBinding


INTERNET_CATEGORY_AR = 'خدمات الإنترنت'
INTERNET_SECTION_AR = 'الإنترنت'


def _customer_catalog_enabled(package):
    return bool(
        package.is_active
        and package.visible_to_customer
        and package.activation_policy != InternetPackage.ActivationPolicy.MANUAL
        and package.access_mode != InternetPackage.AccessMode.MEMBERSHIP_CREDIT
    )


def _internet_category():
    category = Category.objects.filter(name_ar=INTERNET_CATEGORY_AR).order_by('pk').first()
    if category is None:
        category = Category.objects.create(name_ar=INTERNET_CATEGORY_AR, name_en='Internet services')
    return category


def _internet_section():
    section = MenuSection.objects.filter(name_ar=INTERNET_SECTION_AR).order_by('pk').first()
    if section is None:
        section = MenuSection.objects.create(
            name_ar=INTERNET_SECTION_AR,
            name_en='Internet',
            description_ar='باقات الإنترنت المتاحة داخل هَبّ.',
            sort_order=900,
            is_active=True,
            visible_on_qr=True,
        )
    return section


@transaction.atomic
def ensure_package_catalog_product(package):
    """Return and synchronize the stable Product identity for one package.

    The Product remains useful for accounting, OrderItem history, receipts and
    reporting. Customer presentation is intentionally handled by the dedicated
    table Internet quick-start flow rather than a generic product card.
    """
    package = InternetPackage.objects.select_for_update().get(pk=package.pk)
    binding = InternetCatalogBinding.objects.select_for_update().filter(package=package).first()
    category = _internet_category()

    if binding:
        product = Product.objects.select_for_update().get(pk=binding.product_id)
    else:
        product = (
            Product.objects.select_for_update(of=('self',))
            .filter(category=category, name_ar=package.name_ar, product_type=Product.ProductType.INTERNET)
            .filter(internet_catalog_binding__isnull=True)
            .order_by('pk')
            .first()
        )
        if product is None:
            product = Product.objects.create(
                category=category,
                name_ar=package.name_ar,
                price_syp=package.price_syp,
                product_type=Product.ProductType.INTERNET,
                item_type=Product.ItemType.SERVICE,
                service_type=Product.ServiceType.INTERNET,
            )
        binding = InternetCatalogBinding.objects.create(package=package, product=product)

    customer_orderable = _customer_catalog_enabled(package)
    product.category = category
    product.name_ar = package.name_ar
    product.name_en = getattr(package, 'name_en', '') or ''
    product.description_ar = getattr(package, 'description_ar', '') or ''
    product.description_en = getattr(package, 'description_en', '') or ''
    product.price_syp = int(package.price_syp)
    product.is_available = bool(package.is_active)
    product.sort_order = package.sort_order
    product.product_type = Product.ProductType.INTERNET
    product.item_type = Product.ItemType.SERVICE
    product.service_type = Product.ServiceType.INTERNET
    product.requires_preparation = False
    product.visible_on_pos = False
    product.orderable_on_pos = False
    # Keep the historical catalog flags synchronized for compatibility. The
    # public menu decorator below deliberately suppresses bound Internet products.
    product.visible_on_qr = customer_orderable
    product.orderable_on_qr = customer_orderable
    product.available_for_events = False
    product.available_for_takeaway = False
    product.not_discountable = True
    product.track_margin = False
    product.save(update_fields=[
        'category', 'name_ar', 'name_en', 'description_ar', 'description_en',
        'price_syp', 'is_available', 'sort_order', 'product_type', 'item_type',
        'service_type', 'requires_preparation', 'visible_on_pos', 'orderable_on_pos',
        'visible_on_qr', 'orderable_on_qr', 'available_for_events',
        'available_for_takeaway', 'not_discountable', 'track_margin', 'updated_at',
    ])
    product.menu_sections.add(_internet_section())
    return product


def decorate_menu_context(context, *, table):
    """Prepare the customer menu while keeping identity independent of benefits.

    A trusted member device remains a signed-in account even when its membership
    expires.  ``member_identity_context`` therefore always carries recognized
    identity, while the legacy ``member_context`` key is kept only when an active
    plan exists so existing menu pricing/benefit UI cannot claim expired benefits.

    Bound Internet identities are also removed from the ordinary food/drink
    catalog because Internet starts through the dedicated table flow.
    """
    identity_context = context.get('member_context')
    if identity_context:
        context['member_identity_context'] = identity_context
        if not getattr(identity_context, 'plan', None):
            context['member_context'] = None

    internet_product_ids = set(
        InternetCatalogBinding.objects.values_list('product_id', flat=True)
    )
    filtered_sections = []
    for section, products in context.get('section_products', []):
        visible_products = [
            product for product in products
            if product.pk not in internet_product_ids
        ]
        if visible_products:
            filtered_sections.append((section, visible_products))

    context['section_products'] = filtered_sections
    context['internet_self_service_enabled'] = False
    context['internet_packages'] = []
    context['internet_catalog_ordering_enabled'] = False
    return context


def _internet_items(order):
    items = list(order.items.select_related('product').order_by('pk'))
    if not items:
        return []
    bindings = {
        binding.product_id: binding
        for binding in InternetCatalogBinding.objects.select_related('package').filter(
            product_id__in=[item.product_id for item in items]
        )
    }
    return [(item, bindings[item.product_id].package) for item in items if item.product_id in bindings]


@transaction.atomic
def fulfill_internet_items_for_order(order):
    """Fulfill any legacy Internet cart line on the already-created customer Order.

    This compatibility path remains atomic for existing clients/bookmarks even
    though the current table UI no longer presents Internet as a generic product.
    """
    order = (
        Order.objects.select_related('visit', 'member', 'table')
        .select_for_update(of=('self',))
        .get(pk=order.pk)
    )
    pairs = _internet_items(order)
    if not pairs:
        return []

    if not self_service_enabled(get_system_settings()):
        raise ValidationError('خدمة الإنترنت الذاتية غير متاحة حالياً.')
    if not order.table_id or not order.visit_id or order.visit.status != order.visit.Status.OPEN:
        raise ValidationError('باقات الإنترنت متاحة من QR الطاولة أثناء جلسة مفتوحة فقط.')
    if len(pairs) != 1 or pairs[0][0].quantity != 1:
        raise ValidationError('اختر باقة إنترنت واحدة فقط في كل طلب.')

    item, package = pairs[0]
    package = InternetPackage.objects.select_for_update().get(pk=package.pk)
    error = package_customer_error(package, order.member)
    if error:
        raise ValidationError(error)

    charged_price, pricing_benefit = resolve_internet_price(order.member, package)
    charged_price = int(charged_price)
    if item.unit_price_syp_snapshot != charged_price or item.line_total_syp_snapshot != charged_price:
        item.unit_price_syp_snapshot = charged_price
        item.line_total_syp_snapshot = charged_price
        item.product_name_ar_snapshot = package.name_ar
        item.product_name_en_snapshot = getattr(package, 'name_en', '') or ''
        item.save(update_fields=[
            'unit_price_syp_snapshot', 'line_total_syp_snapshot',
            'product_name_ar_snapshot', 'product_name_en_snapshot', 'updated_at',
        ])

    entitlement_key = f'menu-order-item:{item.pk}'
    entitlement = create_entitlement(
        package,
        member=order.member,
        order=order,
        visit=order.visit,
        idempotency_key=entitlement_key,
        charged_amount_syp=charged_price,
        pricing_benefit=pricing_benefit,
    )
    if entitlement.order_id != order.pk or entitlement.visit_id != order.visit_id:
        raise ValidationError('تعذر ربط باقة الإنترنت بهذا الطلب.')

    active = entitlement.sessions.filter(
        status=InternetSession.Status.ACTIVE,
        visit_id=order.visit_id,
    ).first()
    if active is None:
        if entitlement.sessions.exists():
            raise ValidationError('تم استخدام هذه الباقة مسبقاً.')
        active = start_usage_session(entitlement, visit=order.visit)

    enqueue_network_operation(
        entitlement,
        InternetNetworkOperation.Operation.PROVISION,
        idempotency_key=f'entitlement:{entitlement.public_code}:network:provision',
    )
    now = timezone.now()
    type(order.visit).objects.filter(pk=order.visit_id).update(last_activity_at=now)
    ActivityLog.objects.create(
        action='visit.internet_menu_item_fulfilled',
        details={
            'visit_id': order.visit_id,
            'order_id': order.pk,
            'order_item_id': item.pk,
            'entitlement_id': entitlement.pk,
            'session_id': active.pk,
            'package_id': package.pk,
        },
    )
    return [(entitlement, active)]
