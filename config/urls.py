from django.contrib import admin
from django.urls import path
from core.views import (
    dashboard,
    menu_public,
    menu_table,
    order_public,
    staff_orders,
    staff_order_status,
    staff_cashier,
    staff_cashier_order,
    staff_cashier_pay,
)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', dashboard, name='dashboard'),
    path('menu/', menu_public, name='menu_public'),
    path('menu/table/<uuid:qr_token>/', menu_table, name='menu_table'),
    path('order/<uuid:public_code>/', order_public, name='order_public'),
    path('staff/orders/', staff_orders, name='staff_orders'),
    path('staff/orders/partial/', staff_orders, name='staff_orders_partial'),
    path('staff/orders/<uuid:public_code>/status/', staff_order_status, name='staff_order_status'),
    path('staff/cashier/', staff_cashier, name='staff_cashier'),
    path('staff/cashier/<uuid:public_code>/', staff_cashier_order, name='staff_cashier_order'),
    path('staff/cashier/<uuid:public_code>/pay/', staff_cashier_pay, name='staff_cashier_pay'),
]
