from django.urls import path

from core.views.staff_recipes import staff_recipe_edit, staff_recipe_manager


urlpatterns = [
    path('', staff_recipe_manager, name='staff_recipe_manager'),
    path('<int:product_id>/', staff_recipe_edit, name='staff_recipe_edit'),
]
