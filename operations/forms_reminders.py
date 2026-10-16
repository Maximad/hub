"""Compact Arabic reminder creation form, fully validated on the server."""

from django import forms

from .models import BusinessDayDutyCoverage, Reminder
from .opening import active_staff_queryset


class ReminderCreateForm(forms.Form):
    title_ar = forms.CharField(
        label='عنوان التذكير',
        max_length=240,
        widget=forms.TextInput(attrs={'placeholder': 'مثال: التأكد من تجهيز القاعة'}),
    )
    details = forms.CharField(
        label='تفاصيل اختيارية',
        required=False,
        widget=forms.Textarea(attrs={'rows': 2}),
    )
    due_at = forms.DateTimeField(
        label='موعد أول تذكير',
        input_formats=['%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S'],
        widget=forms.DateTimeInput(
            format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'},
        ),
    )
    repeat = forms.ChoiceField(
        label='التكرار',
        choices=Reminder.Repeat.choices,
        initial=Reminder.Repeat.ONCE,
    )
    interval_minutes = forms.IntegerField(
        label='عدد الدقائق إذا كان التكرار بفترة',
        required=False, min_value=1, max_value=10080,
        widget=forms.NumberInput(attrs={'min': 1, 'max': 10080}),
    )
    responsibility = forms.ChoiceField(
        label='المسؤولية',
        choices=[('', 'التشغيل العام')] + list(BusinessDayDutyCoverage.Responsibility.choices),
        required=False,
    )
    assigned_to = forms.ModelChoiceField(
        label='الموظف (اختياري)',
        queryset=active_staff_queryset().none(),
        required=False,
        empty_label='حسب المسؤولية المناوبة',
    )
    expires_at = forms.DateTimeField(
        label='انتهاء الصلاحية (اختياري)',
        required=False,
        input_formats=['%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S'],
        widget=forms.DateTimeInput(
            format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'},
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assigned_to'].queryset = active_staff_queryset()

    def clean(self):
        data = super().clean()
        interval = data.get('interval_minutes')
        repeat = data.get('repeat')
        if repeat == Reminder.Repeat.INTERVAL and interval is None:
            self.add_error('interval_minutes', 'أدخل المدة بالدقائق لهذا التكرار.')
        elif repeat and repeat != Reminder.Repeat.INTERVAL and interval is not None:
            self.add_error('interval_minutes', 'اترك الدقائق فارغة إذا لم تختر التكرار بفترة.')
        due, expires = data.get('due_at'), data.get('expires_at')
        if due and expires and expires <= due:
            self.add_error('expires_at', 'وقت انتهاء الصلاحية يجب أن يكون بعد الموعد.')
        return data
