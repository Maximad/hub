"""Sprint B public field allowlists. Publication never follows operational state alone."""
from dataclasses import dataclass
from datetime import datetime

from django.db.models import F, Prefetch, Q
from django.urls import reverse
from django.utils import timezone

from catalog.models import MediaAsset
from core.services.public_homepage import DAMASCUS
from members.models import MembershipBenefitRule, MembershipPlan, Program
from members.plan_presentation import plan_term_summary
from members.plan_readiness import assess_membership_plan
from vendors.models import VendorMedia, VendorParticipation


@dataclass(frozen=True)
class PublicDiscoveryItem:
    title: str
    url: str
    description: str = ''
    name: str = ''
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    price_syp: int | None = None
    term: str = ''
    image_url: str = ''
    image_alt: str = ''


def local_date(value):
    return timezone.localtime(value, DAMASCUS) if value else None


def public_programs(now):
    return Program.objects.filter(is_public=True, is_active=True, status=Program.Status.OPEN).filter(
        Q(ends_at__isnull=True) | Q(ends_at__gt=now)
    ).order_by(F('starts_at').asc(nulls_last=True), 'pk')


def program_item(program):
    return PublicDiscoveryItem(
        title=program.name_ar, description=program.description_ar,
        url=reverse('public_program_detail', kwargs={'code': program.code}),
        starts_at=local_date(program.starts_at), ends_at=local_date(program.ends_at),
    )


def public_popups(now):
    media = VendorMedia.objects.filter(
        is_public=True, is_active=True, media_asset__is_active=True,
        role__in=[VendorMedia.Role.COVER, VendorMedia.Role.LOGO],
        media_asset__media_type__in=[MediaAsset.MediaType.IMAGE, MediaAsset.MediaType.GIF],
    ).select_related('media_asset').order_by('-is_primary', 'sort_order', 'pk')
    return VendorParticipation.objects.filter(
        is_public=True, vendor__is_public=True, vendor__is_active=True,
        status=VendorParticipation.Status.ACTIVE,
    ).filter(Q(starts_at__gte=now) | Q(starts_at__lt=now, ends_at__gt=now)).select_related(
        'vendor'
    ).prefetch_related(Prefetch('vendor__media', queryset=media, to_attr='public_media')).order_by('starts_at', 'pk')


def popup_item(participation):
    image_url = image_alt = ''
    for media in participation.vendor.public_media:
        url = media.media_asset.safe_url
        if url:
            image_url = url
            image_alt = media.media_asset.alt_text_ar or participation.vendor.name_ar
            break
    # Do not use vendor.__str__ (its fallback can be a phone number), notes,
    # linked events/locations, commissions, or supplier contact information.
    return PublicDiscoveryItem(
        title=participation.title_ar, name=participation.vendor.name_ar,
        url=reverse('public_popup_detail', kwargs={'uuid': participation.uuid}),
        starts_at=local_date(participation.starts_at), ends_at=local_date(participation.ends_at),
        image_url=image_url, image_alt=image_alt,
    )


def public_membership_candidates():
    rules = MembershipBenefitRule.objects.select_related('product', 'category', 'internet_bandwidth_profile')
    return MembershipPlan.objects.filter(is_active=True, visible_to_customer=True).prefetch_related(
        Prefetch('benefit_rules', queryset=rules)
    ).order_by('sort_order', 'pk')


def membership_item(plan):
    # Reuse the commercial preflight, but never expose its internal findings or
    # staff benefit labels. A visible plan with a known blocking error is hidden.
    if not assess_membership_plan(plan)['can_sell']:
        return None
    return PublicDiscoveryItem(
        title=plan.name_ar, description=plan.description_ar,
        url=reverse('public_membership_detail', kwargs={'code': plan.code}),
        price_syp=plan.price_syp, term=plan_term_summary(plan),
    )


def public_discovery_context(*, now):
    # Keep homepage work bounded. Commercial preflight is evaluated only for
    # these three explicitly visible candidates, never an entire plan catalog.
    memberships = tuple(item for plan in public_membership_candidates()[:3]
                        if (item := membership_item(plan)) is not None)
    return {
        'programs': tuple(program_item(p) for p in public_programs(now)[:3]),
        'popups': tuple(popup_item(p) for p in public_popups(now)[:3]),
        'memberships': memberships,
    }
