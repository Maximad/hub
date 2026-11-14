from django.urls import path

from member_accounts import card_views, views
from core.views.customer_service import customer_visit_request
from core.views.event_attendance import member_attendance_history


urlpatterns = [
    path('join/<str:token>/', views.join_invitation, name='member_account_join'),
    path('me/login/', views.member_login, name='member_account_login'),
    path('me/login/<uuid:challenge_id>/', views.member_login_verify, name='member_account_login_verify'),
    path('me/card/qr.svg', card_views.member_card_qr, name='member_card_qr'),
    path('me/', views.member_home, name='member_account_home'),
    path('me/attendance/', member_attendance_history, name='member_attendance_history'),
    path('me/logout/', views.member_logout, name='member_account_logout'),
    path('me/devices/<uuid:device_id>/revoke/', views.member_revoke_device, name='member_account_revoke_device'),
    path('me/orders/<uuid:public_code>/', views.member_order_detail, name='member_account_order_detail'),
    path('visit/request-service/', customer_visit_request, name='customer_visit_request'),
    path('staff/member-card/<str:token>/', card_views.staff_member_card_scan, name='staff_member_card_scan'),
    path('staff/member-invitations/new/', views.staff_invitation_new, name='staff_member_invitation_new'),
    path('staff/members/<uuid:member_id>/invite/', views.staff_invitation_new, name='staff_member_invitation_existing'),
    path('staff/members/<uuid:member_id>/account/lock/', views.staff_lock_account, name='staff_member_account_lock'),
]
