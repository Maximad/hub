from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '__first__'),
        ('events', '__first__'),
        ('vendors', '__first__'),
    ]

    operations = [
        migrations.CreateModel(
            name='MenuSection',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('name_ar', models.CharField(max_length=120)),
                ('name_en', models.CharField(blank=True, max_length=120)),
                ('description_ar', models.TextField(blank=True)),
                ('sort_order', models.IntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                ('visible_on_qr', models.BooleanField(default=True)),
                ('parent', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='children', to='catalog.menusection')),
            ],
        ),
        migrations.CreateModel(
            name='PrepStation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('code', models.SlugField(unique=True)),
                ('name_ar', models.CharField(max_length=120)),
                ('name_en', models.CharField(blank=True, max_length=120)),
                ('is_active', models.BooleanField(default=True)),
                ('sort_order', models.IntegerField(default=0)),
            ],
        ),
        migrations.CreateModel(
            name='Tag',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('code', models.SlugField(unique=True)),
                ('name_ar', models.CharField(max_length=120)),
                ('name_en', models.CharField(blank=True, max_length=120)),
                ('tag_type', models.CharField(default='product', max_length=40)),
                ('is_active', models.BooleanField(default=True)),
            ],
        ),
        migrations.CreateModel(
            name='ProductAvailability',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('starts_at', models.DateTimeField(blank=True, null=True)),
                ('ends_at', models.DateTimeField(blank=True, null=True)),
                ('available_from_time', models.TimeField(blank=True, null=True)),
                ('available_until_time', models.TimeField(blank=True, null=True)),
                ('days_of_week', models.JSONField(blank=True, default=list)),
                ('is_active', models.BooleanField(default=True)),
                ('notes', models.TextField(blank=True)),
                ('event', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='events.event')),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='availability_rules', to='core.product')),
                ('vendor_participation', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='vendors.vendorparticipation')),
            ],
        ),
    ]
