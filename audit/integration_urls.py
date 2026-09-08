from django.urls import path

from .integration_api import (
    catalog_apply,
    catalog_preview,
    inventory_list,
    management_schema,
    products_list,
    recipes_list,
)

app_name = 'management_api'

urlpatterns = [
    path('schema/', management_schema, name='schema'),
    path('catalog/products/', products_list, name='products'),
    path('inventory/items/', inventory_list, name='inventory_items'),
    path('recipes/', recipes_list, name='recipes'),
    path('catalog/preview/', catalog_preview, name='catalog_preview'),
    path('catalog/apply/', catalog_apply, name='catalog_apply'),
]
