from django.contrib import admin
from .models import (
    Room, TableArea, Category, Product, Order, OrderItem, Payment,
    Member, InternetPackage, InternetSession, Shift, ActivityLog,
)

admin.site.register([Room, TableArea, Category, Product, Order, OrderItem, Payment, Member, InternetPackage, InternetSession, Shift, ActivityLog])
