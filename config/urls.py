from django.contrib import admin
from django.urls import path
from core.views import menu
from core.views.staff_reports import staff_reports_home, staff_reports_day, staff_reports_day_csv, staff_close_day
from core.views.staff_internet import staff_internet, staff_internet_start, staff_internet_session, staff_internet_end, staff_wifi
from core.views.staff_members import staff_members, staff_member_new, staff_member_detail, staff_member_subscribe
from core.views.staff_events import staff_events, staff_event_new, staff_event_detail
from core.views.staff_reservations import staff_reservations, staff_reservation_new, staff_reservation_detail, staff_reservation_status
from core.views.staff_vendors import staff_vendors, staff_vendor_new, staff_vendor_detail, staff_vendor_participation_new


urlpatterns = [
    path('admin/', admin.site.urls),
    path('', menu.dashboard, name='dashboard'),
    path('menu/', menu.menu_public, name='menu_public'),
    path('menu/table/<uuid:qr_token>/', menu.menu_table, name='menu_table'),
    path('order/<uuid:public_code>/', menu.order_public, name='order_public'),
    path('staff/', menu.staff_home, name='staff_home'),
    path('staff/pos/', menu.staff_pos, name='staff_pos'),
    path('staff/qr/', menu.staff_qr_links, name='staff_qr_links'),
    path('staff/qr/print/', menu.staff_qr_print, name='staff_qr_print'),
    path('staff/menu-tools/', menu.staff_menu_tools, name='staff_menu_tools'),
    path('staff/modifiers/', menu.staff_modifiers, name='staff_modifiers'),
    path('staff/orders/', menu.staff_orders, name='staff_orders'),
    path('staff/orders/partial/', menu.staff_orders, name='staff_orders_partial'),
    path('staff/orders/<uuid:public_code>/status/', menu.staff_order_status, name='staff_order_status'),
    path('staff/cashier/', menu.staff_cashier, name='staff_cashier'),
    path('staff/cashier/<uuid:public_code>/', menu.staff_cashier_order, name='staff_cashier_order'),
    path('staff/cashier/<uuid:public_code>/pay/', menu.staff_cashier_pay, name='staff_cashier_pay'),
    path('staff/reports/', staff_reports_home, name='staff_reports_home'),
    path('staff/reports/day/', staff_reports_day, name='staff_reports_day'),
    path('staff/reports/day.csv', staff_reports_day_csv, name='staff_reports_day_csv'),
    path('staff/close-day/', staff_close_day, name='staff_close_day'),
    path('staff/members/', staff_members, name='staff_members'),
    path('staff/members/new/', staff_member_new, name='staff_member_new'),
    path('staff/members/<str:member_id>/', staff_member_detail, name='staff_member_detail'),
    path('staff/members/<str:member_id>/subscribe/', staff_member_subscribe, name='staff_member_subscribe'),
    path('staff/internet/', staff_internet, name='staff_internet'),
    path('staff/internet/start/', staff_internet_start, name='staff_internet_start'),
    path('staff/internet/<int:session_id>/', staff_internet_session, name='staff_internet_session'),
    path('staff/internet/<int:session_id>/end/', staff_internet_end, name='staff_internet_end'),
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
