from django.contrib import admin
from django.urls import path
from core.views import dashboard, menu_public, menu_table, order_public

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', dashboard, name='dashboard'),
    path('menu/', menu_public, name='menu_public'),
    path('menu/table/<uuid:qr_token>/', menu_table, name='menu_table'),
    path('order/<uuid:public_code>/', order_public, name='order_public'),
]
