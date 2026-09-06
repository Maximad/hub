from django.urls import path

from member_accounts import views


urlpatterns = [
    path('join/<str:token>/', views.join_invitation, name='member_account_join'),
    path('me/login/', views.member_login, name='member_account_login'),
    path('me/login/<uuid:challenge_id>/', views.member_login_verify, name='member_account_login_verify'),
    path('me/', views.member_home, name='member_account_home'),
    path('me/logout/', views.member_logout, name='member_account_logout'),
    path('staff/member-invitations/new/', views.staff_invitation_new, name='staff_member_invitation_new'),
    path('staff/members/<uuid:member_id>/invite/', views.staff_invitation_new, name='staff_member_invitation_existing'),
    path('staff/members/<uuid:member_id>/account/lock/', views.staff_lock_account, name='staff_member_account_lock'),
]
