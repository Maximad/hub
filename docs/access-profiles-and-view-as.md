# Access profiles, job titles, and View As

Hub separates **what a person is called** from **what their account can do**.

## Job title

`User.job_title` is descriptive only. Examples:

- مصور
- مدير فعاليات
- مسؤول صوت
- منسق مجتمع
- مسؤول إنتاج

Changing a job title never grants or removes permissions.

## Access profile

`AccessProfile` is the reusable baseline permission set. Each profile contains:

- Arabic display name
- technical code
- description
- capabilities
- active/inactive assignment state
- an internal compatibility/routing family

The routing family exists only because a small amount of notification/operational
code still distinguishes service, cashier, preparation, bar, admin and provider
families. New cross-functional profiles should normally use **فريق عام** unless
they need one of those routing behaviors.

The seeded profiles preserve the pre-migration behavior:

- إدارة كاملة
- تشغيل وكاشير
- خدمة وتنظيم
- تحضير وتشغيل خلفي
- بار وتحضير
- مزوّد إنترنت خارجي
- فريق عام

Superusers can add and edit profiles from Django admin under
**Accounts → Access profiles**. Editing the capability list changes the baseline
for every assigned user. Per-user allow/deny overrides continue to take priority.

A profile's internal routing family cannot be changed while users are assigned to
it. This prevents notification/provider behavior from silently changing under
existing accounts.

## View As

A Django superuser can choose **View As** from the user list, user detail page, or
Django admin user list.

View As:

- keeps the real authenticated session as the superuser;
- renders staff/provider pages with the target user's identity and effective
  permissions;
- displays a persistent warning banner;
- blocks POST/PUT/PATCH/DELETE and provider logout while preview is active;
- does not send browser-notification registration requests because notification
  JavaScript is disabled in preview;
- writes start/stop audit events under the real superuser.

It is intentionally a **read-only preview**, not impersonation for operational
actions. End the preview before changing orders, payments, users, settings or any
other data.
