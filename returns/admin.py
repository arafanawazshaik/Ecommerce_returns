"""
Returns Module - Django Admin Configuration

This is the internal admin panel used by the operations team to:
- View and search return requests
- Approve/reject returns manually
- Review fraud flags
- Override return statuses for exception cases
- View customer-uploaded images

Access at: http://127.0.0.1:8000/admin/
"""

from django.contrib import admin
from .models import Order, ReturnRequest, ReturnImage, ReturnStatusHistory, FraudFlag


# ============================================================
# INLINE MODELS (shown inside parent model's page)
# ============================================================

class ReturnImageInline(admin.TabularInline):
    """Show images inside the ReturnRequest detail page."""
    model = ReturnImage
    extra = 0
    readonly_fields = ['image_key', 'image_url', 'file_name', 'file_size', 'uploaded_at']


class ReturnStatusHistoryInline(admin.TabularInline):
    """Show status timeline inside the ReturnRequest detail page."""
    model = ReturnStatusHistory
    extra = 0
    readonly_fields = ['from_status', 'to_status', 'changed_by', 'comment', 'created_at']
    ordering = ['-created_at']


class FraudFlagInline(admin.TabularInline):
    """Show fraud flags inside the ReturnRequest detail page."""
    model = FraudFlag
    extra = 0
    readonly_fields = ['flag_type', 'description', 'created_at']
    fields = ['flag_type', 'status', 'description', 'reviewed_by', 'review_notes', 'created_at']


# ============================================================
# ORDER ADMIN
# ============================================================

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = [
        'order_number', 'customer_name', 'product_name',
        'category', 'total_amount', 'status', 'delivered_at',
    ]
    list_filter = ['status', 'category', 'payment_method']
    search_fields = ['order_number', 'customer_name', 'customer_email', 'product_name']
    readonly_fields = ['created_at', 'updated_at']
    list_per_page = 25

    fieldsets = (
        ('Order Info', {
            'fields': ('order_number', 'status', 'ordered_at', 'delivered_at')
        }),
        ('Customer Info', {
            'fields': ('customer_id', 'customer_name', 'customer_email', 'customer_phone')
        }),
        ('Product Info', {
            'fields': ('product_name', 'product_sku', 'category', 'quantity', 'unit_price', 'total_amount')
        }),
        ('Payment & Shipping', {
            'fields': ('payment_method', 'payment_reference', 'shipping_address', 'shipping_pincode')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )


# ============================================================
# RETURN REQUEST ADMIN
# ============================================================

@admin.register(ReturnRequest)
class ReturnRequestAdmin(admin.ModelAdmin):
    list_display = [
        'return_number', 'get_order_number', 'customer_name',
        'reason', 'status', 'refund_amount', 'is_flagged',
        'is_high_value', 'created_at',
    ]
    list_filter = ['status', 'reason', 'is_flagged', 'is_high_value', 'refund_method']
    search_fields = ['return_number', 'customer_name', 'customer_email', 'order__order_number']
    readonly_fields = ['return_number', 'created_at', 'updated_at']
    list_per_page = 25

    # Show images, status history, and fraud flags inside return detail page
    inlines = [ReturnImageInline, ReturnStatusHistoryInline, FraudFlagInline]

    fieldsets = (
        ('Return Info', {
            'fields': ('return_number', 'order', 'status', 'reason', 'reason_description')
        }),
        ('Customer Info', {
            'fields': ('customer_id', 'customer_name', 'customer_email')
        }),
        ('Refund Info', {
            'fields': ('refund_method', 'refund_amount', 'refund_reference')
        }),
        ('Pickup Info', {
            'fields': (
                'pickup_address', 'pickup_pincode', 'pickup_scheduled_date',
                'pickup_completed_date', 'logistics_partner', 'tracking_number'
            )
        }),
        ('Flags', {
            'fields': ('is_flagged', 'is_high_value', 'idempotency_key')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    def get_order_number(self, obj):
        return obj.order.order_number
    get_order_number.short_description = 'Order Number'

    # Custom actions for bulk operations
    actions = ['approve_returns', 'reject_returns', 'mark_as_pickup_scheduled']

    @admin.action(description='Approve selected returns')
    def approve_returns(self, request, queryset):
        updated = queryset.filter(status='pending').update(status='approved')
        for ret in queryset.filter(status='approved'):
            ReturnStatusHistory.objects.create(
                return_request=ret,
                from_status='pending',
                to_status='approved',
                changed_by=f'admin:{request.user.username}',
                comment='Bulk approved via admin panel'
            )
        self.message_user(request, f'{updated} return(s) approved.')

    @admin.action(description='Reject selected returns')
    def reject_returns(self, request, queryset):
        updated = queryset.filter(status='pending').update(status='rejected')
        for ret in queryset.filter(status='rejected'):
            ReturnStatusHistory.objects.create(
                return_request=ret,
                from_status='pending',
                to_status='rejected',
                changed_by=f'admin:{request.user.username}',
                comment='Rejected via admin panel'
            )
        self.message_user(request, f'{updated} return(s) rejected.')

    @admin.action(description='Mark as Pickup Scheduled')
    def mark_as_pickup_scheduled(self, request, queryset):
        updated = queryset.filter(status='approved').update(status='pickup_scheduled')
        self.message_user(request, f'{updated} return(s) marked for pickup.')


# ============================================================
# FRAUD FLAG ADMIN
# ============================================================

@admin.register(FraudFlag)
class FraudFlagAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'get_return_number', 'customer_id', 'flag_type',
        'status', 'created_at', 'reviewed_by',
    ]
    list_filter = ['flag_type', 'status']
    search_fields = ['customer_id', 'return_request__return_number', 'description']
    list_per_page = 25

    fieldsets = (
        ('Flag Info', {
            'fields': ('return_request', 'customer_id', 'flag_type', 'status', 'description')
        }),
        ('Review', {
            'fields': ('reviewed_by', 'review_notes', 'reviewed_at')
        }),
    )

    def get_return_number(self, obj):
        return obj.return_request.return_number
    get_return_number.short_description = 'Return Number'


# ============================================================
# STATUS HISTORY ADMIN (standalone view)
# ============================================================

@admin.register(ReturnStatusHistory)
class ReturnStatusHistoryAdmin(admin.ModelAdmin):
    list_display = [
        'get_return_number', 'from_status', 'to_status',
        'changed_by', 'created_at',
    ]
    list_filter = ['to_status', 'changed_by']
    search_fields = ['return_request__return_number', 'comment']
    readonly_fields = ['return_request', 'from_status', 'to_status', 'changed_by', 'comment', 'created_at']
    list_per_page = 50

    def get_return_number(self, obj):
        return obj.return_request.return_number
    get_return_number.short_description = 'Return Number'