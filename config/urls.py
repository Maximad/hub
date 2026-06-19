from django.conf import settings
from django.contrib import admin
from django.views.static import serve
from django.urls import path, re_path
from core.views import menu
from core.views.staff_reports import staff_reports_home, staff_reports_day, staff_reports_day_csv, staff_product_margin_report, staff_product_margin_csv, staff_close_day
from core.views.staff_internet import staff_internet, staff_internet_start, staff_internet_session, staff_internet_end, staff_internet_cancel, staff_wifi
from core.views.staff_members import staff_members, staff_member_new, staff_member_detail, staff_member_subscribe
from core.views.staff_events import staff_events, staff_event_new, staff_event_detail
from core.views.staff_reservations import staff_reservations, staff_reservation_new, staff_reservation_detail, staff_reservation_status
from core.views.staff_vendors import staff_vendors, staff_vendor_new, staff_vendor_detail, staff_vendor_participation_new
from core.views.staff_import import (
    staff_import_home, staff_import_upload, staff_import_template, staff_import_preview, staff_import_confirm,
)
from core.views.kitchen import staff_kitchen, staff_kitchen_partial, staff_kitchen_order, staff_kitchen_item_status, staff_prep, staff_prep_station
from core.views.staff_finance import staff_finance_home, staff_expenses, staff_expense_new, staff_cashbox, staff_cashbox_new, staff_expenses_csv, staff_cashbox_csv
from core.views_notifications import staff_notifications, staff_notifications_poll, staff_notifications_mark_read, staff_notifications_preferences
from accounts.views_staff import (
    staff_users_list, staff_user_new, staff_user_detail, staff_user_edit, staff_user_password, staff_user_toggle_active,
)


urlpatterns = [
    path('admin/', admin.site.urls),
    path('', menu.dashboard, name='dashboard'),
    path('menu/', menu.menu_public, name='menu_public'),
    path('menu/table/<uuid:qr_token>/', menu.menu_table, name='menu_table'),
    path('order/<uuid:public_code>/', menu.order_public, name='order_public'),
    path('order/<uuid:public_code>/qr.svg', menu.order_qr, name='order_qr'),
    path('table/<uuid:qr_token>/qr.svg', menu.table_qr, name='table_qr'),
    path('staff/', menu.staff_home, name='staff_home'),
    path('staff/pos/', menu.staff_pos, name='staff_pos'),
    path('staff/notifications/', staff_notifications, name='staff_notifications'),
    path('staff/notifications/poll/', staff_notifications_poll, name='staff_notifications_poll'),
    path('staff/notifications/mark-read/', staff_notifications_mark_read, name='staff_notifications_mark_read'),
    path('staff/notifications/preferences/', staff_notifications_preferences, name='staff_notifications_preferences'),
    path('staff/qr/', menu.staff_qr_links, name='staff_qr_links'),
    path('staff/qr/print/', menu.staff_qr_print, name='staff_qr_print'),
    path('staff/menu-tools/', menu.staff_menu_tools, name='staff_menu_tools'),
    path('staff/menu-tools/preview/', menu.staff_menu_tools_preview, name='staff_menu_tools_preview'),
    path('staff/menu-tools/apply/', menu.staff_menu_tools_apply, name='staff_menu_tools_apply'),
    path('staff/users/', staff_users_list, name='staff_users_list'),
    path('staff/users/new/', staff_user_new, name='staff_user_new'),
    path('staff/users/<int:user_id>/', staff_user_detail, name='staff_user_detail'),
    path('staff/users/<int:user_id>/edit/', staff_user_edit, name='staff_user_edit'),
    path('staff/users/<int:user_id>/password/', staff_user_password, name='staff_user_password'),
    path('staff/users/<int:user_id>/toggle-active/', staff_user_toggle_active, name='staff_user_toggle_active'),
    path('staff/import/', staff_import_home, name='staff_import_home'),
    path('staff/import/<str:import_type>/', staff_import_upload, name='staff_import_upload'),
    path('staff/import/<str:import_type>/template.csv', staff_import_template, name='staff_import_template'),
    path('staff/import/<str:import_type>/preview/', staff_import_preview, name='staff_import_preview'),
    path('staff/import/<str:import_type>/confirm/', staff_import_confirm, name='staff_import_confirm'),
    path('staff/modifiers/', menu.staff_modifiers, name='staff_modifiers'),
    path('staff/prep/', staff_prep, name='staff_prep'),
    path('staff/prep/<slug:station_code>/', staff_prep_station, name='staff_prep_station'),
    path('staff/kitchen/', staff_kitchen, name='staff_kitchen'),
    path('staff/kitchen/partial/', staff_kitchen_partial, name='staff_kitchen_partial'),
    path('staff/kitchen/order/<uuid:public_code>/', staff_kitchen_order, name='staff_kitchen_order'),
    path('staff/kitchen/item/<int:item_id>/status/', staff_kitchen_item_status, name='staff_kitchen_item_status'),
    path('staff/orders/', menu.staff_orders, name='staff_orders'),
    path('staff/delivery/', menu.staff_delivery, name='staff_delivery'),
    path('staff/orders/partial/', menu.staff_orders, name='staff_orders_partial'),
    path('staff/orders/<uuid:public_code>/status/', menu.staff_order_status, name='staff_order_status'),
    path('staff/orders/<uuid:public_code>/edit/', menu.staff_order_edit, name='staff_order_edit'),
    path('staff/orders/<uuid:public_code>/edit/add/', menu.staff_order_edit_add_item, name='staff_order_edit_add_item'),
    path('staff/orders/<uuid:public_code>/edit/items/<int:item_id>/update/', menu.staff_order_edit_update_item, name='staff_order_edit_update_item'),
    path('staff/orders/<uuid:public_code>/edit/items/<int:item_id>/remove/', menu.staff_order_edit_remove_item, name='staff_order_edit_remove_item'),
    path('staff/cashier/', menu.staff_cashier, name='staff_cashier'),
    path('staff/cashier/<uuid:public_code>/', menu.staff_cashier_order, name='staff_cashier_order'),
    path('staff/cashier/<uuid:public_code>/pay/', menu.staff_cashier_pay, name='staff_cashier_pay'),
    path('staff/cashier/<uuid:public_code>/discount/', menu.staff_cashier_discount, name='staff_cashier_discount'),
    path('staff/finance/', staff_finance_home, name='staff_finance_home'),
    path('staff/finance/expenses/', staff_expenses, name='staff_finance_expenses'),
    path('staff/finance/expenses/new/', staff_expense_new, name='staff_finance_expense_new'),
    path('staff/finance/expenses.csv', staff_expenses_csv, name='staff_finance_expenses_csv'),
    path('staff/finance/cashbox/', staff_cashbox, name='staff_finance_cashbox'),
    path('staff/finance/cashbox/new/', staff_cashbox_new, name='staff_finance_cashbox_new'),
    path('staff/finance/cashbox.csv', staff_cashbox_csv, name='staff_finance_cashbox_csv'),
    path('staff/reports/', staff_reports_home, name='staff_reports_home'),
    path('staff/reports/day/', staff_reports_day, name='staff_reports_day'),
    path('staff/reports/day.csv', staff_reports_day_csv, name='staff_reports_day_csv'),
    path('staff/reports/products/', staff_product_margin_report, name='staff_product_margin_report'),
    path('staff/reports/products.csv', staff_product_margin_csv, name='staff_product_margin_csv'),
    path('staff/close-day/', staff_close_day, name='staff_close_day'),
    path('staff/members/', staff_members, name='staff_members'),
    path('staff/members/new/', staff_member_new, name='staff_member_new'),
    path('staff/members/<str:member_id>/', staff_member_detail, name='staff_member_detail'),
    path('staff/members/<str:member_id>/subscribe/', staff_member_subscribe, name='staff_member_subscribe'),
    path('staff/internet/', staff_internet, name='staff_internet'),
    path('staff/internet/start/', staff_internet_start, name='staff_internet_start'),
    path('staff/internet/session/<int:session_id>/', staff_internet_session, name='staff_internet_session'),
    path('staff/internet/<int:session_id>/', staff_internet_session, name='staff_internet_session_legacy'),
    path('staff/internet/session/<int:session_id>/end/', staff_internet_end, name='staff_internet_end'),
    path('staff/internet/<int:session_id>/end/', staff_internet_end, name='staff_internet_end_legacy'),
    path('staff/internet/session/<int:session_id>/cancel/', staff_internet_cancel, name='staff_internet_cancel'),
    path('staff/wifi/', staff_wifi, name='staff_wifi'),
    path('staff/events/', staff_events, name='staff_events'),
    path('staff/events/new/', staff_event_new, name='staff_event_new'),
    path('staff/events/<int:event_id>/', staff_event_detail, name='staff_event_detail'),
    path('staff/reservations/', staff_reservations, name='staff_reservations'),
    path('staff/reservations/new/', staff_reservation_new, name='staff_reservation_new'),
    path('staff/reservations/<int:reservation_id>/', staff_reservation_detail, name='staff_reservation_detail'),
    path('staff/reservations/<int:reservation_id>/status/', staff_reservation_status, name='staff_reservation_status'),
    path('staff/vendors/', staff_vendors, name='staff_vendors'),
    path('staff/vendors/new/', staff_vendor_new, name='staff_vendor_new'),
    path('staff/vendors/<int:vendor_id>/', staff_vendor_detail, name='staff_vendor_detail'),
    path('staff/vendors/<int:vendor_id>/participation/new/', staff_vendor_participation_new, name='staff_vendor_participation_new'),
    path('staff/food-lab/', menu.staff_food_lab, name='staff_food_lab'),
]

# Serving media through Django is acceptable only for small product-image hosting.
# For larger traffic, move media to object storage, a CDN, or dedicated media serving.
urlpatterns += [
    re_path(
        r"^media/(?P<path>.*)$",
        serve,
        {"document_root": settings.MEDIA_ROOT},
    ),
]
