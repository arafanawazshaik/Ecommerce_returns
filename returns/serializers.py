"""
Returns Module - Serializers

Serializers convert Python objects (Django models) to JSON and back.
Think of them as translators:
    - Customer sends JSON → Serializer → Python object → Database
    - Database → Python object → Serializer → JSON response to customer

WHY NOT JUST USE json.dumps()?
Serializers also handle:
    - Validation (is the email valid? is reason a valid choice?)
    - Nested data (return request with images and status history)
    - Field-level control (hide sensitive fields, make fields read-only)
"""

from rest_framework import serializers
from .models import Order, ReturnRequest, ReturnImage, ReturnStatusHistory, FraudFlag


# ============================================================
# ORDER SERIALIZER
# ============================================================

class OrderSerializer(serializers.ModelSerializer):
    """
    Serializes Order data.
    Used when showing order details inside a return response.
    """

    class Meta:
        model = Order
        fields = [
            'id', 'order_number', 'customer_id', 'customer_name',
            'product_name', 'product_sku', 'category', 'quantity',
            'unit_price', 'total_amount', 'status', 'ordered_at',
            'delivered_at', 'payment_method', 'shipping_address',
        ]
        read_only_fields = fields  # Orders are read-only in the returns module


# ============================================================
# RETURN IMAGE SERIALIZER
# ============================================================

class ReturnImageSerializer(serializers.ModelSerializer):
    """Serializes return images (S3 references)."""

    class Meta:
        model = ReturnImage
        fields = [
            'id', 'image_key', 'image_url', 'file_name',
            'file_size', 'content_type', 'uploaded_at',
        ]
        read_only_fields = ['id', 'uploaded_at']


# ============================================================
# RETURN STATUS HISTORY SERIALIZER
# ============================================================

class ReturnStatusHistorySerializer(serializers.ModelSerializer):
    """
    Serializes status history entries.
    This is what customers see as the return timeline.
    """

    class Meta:
        model = ReturnStatusHistory
        fields = [
            'id', 'from_status', 'to_status', 'changed_by',
            'comment', 'created_at',
        ]
        read_only_fields = fields


# ============================================================
# FRAUD FLAG SERIALIZER
# ============================================================

class FraudFlagSerializer(serializers.ModelSerializer):
    """Serializes fraud flags - used by admin/ops team only."""

    class Meta:
        model = FraudFlag
        fields = [
            'id', 'flag_type', 'status', 'description',
            'reviewed_by', 'review_notes', 'created_at', 'reviewed_at',
        ]
        read_only_fields = ['id', 'created_at']


# ============================================================
# CREATE RETURN REQUEST SERIALIZER
# ============================================================
# This handles the INPUT when a customer submits a return

class CreateReturnRequestSerializer(serializers.Serializer):
    """
    Validates the data when a customer creates a new return request.

    What the customer sends (POST body):
    {
        "order_id": 123,
        "reason": "defective",
        "reason_description": "Screen has dead pixels",
        "refund_method": "original",
        "pickup_address": "123 MG Road, Bangalore",
        "pickup_pincode": "560001",
        "idempotency_key": "unique-key-123"
    }
    """

    order_id = serializers.IntegerField(
        help_text="ID of the order to return"
    )
    reason = serializers.ChoiceField(
        choices=ReturnRequest.RETURN_REASON_CHOICES,
        help_text="Reason for return"
    )
    reason_description = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=2000,
        help_text="Detailed explanation (optional)"
    )
    refund_method = serializers.ChoiceField(
        choices=ReturnRequest.REFUND_METHOD_CHOICES,
        default='original',
        help_text="How customer wants the refund"
    )
    pickup_address = serializers.CharField(
        max_length=1000,
        help_text="Address for pickup"
    )
    pickup_pincode = serializers.CharField(
        max_length=10,
        help_text="Pincode for pickup"
    )
    idempotency_key = serializers.CharField(
        required=False,
        max_length=100,
        help_text="Unique key to prevent duplicate submissions"
    )

    def validate_order_id(self, value):
        """Check if order exists."""
        try:
            order = Order.objects.get(id=value)
        except Order.DoesNotExist:
            raise serializers.ValidationError("Order not found.")

        # Order must be delivered before it can be returned
        if order.status != 'delivered':
            raise serializers.ValidationError(
                f"Order status is '{order.status}'. Only delivered orders can be returned."
            )
        return value

    def validate_pickup_pincode(self, value):
        """Basic pincode validation for Indian pincodes."""
        if not value.isdigit() or len(value) != 6:
            raise serializers.ValidationError("Pincode must be exactly 6 digits.")
        return value


# ============================================================
# RETURN REQUEST DETAIL SERIALIZER
# ============================================================
# This handles the OUTPUT - what we send back to the customer

class ReturnRequestSerializer(serializers.ModelSerializer):
    """
    Full return request details including nested order, images, and status history.

    This is what the customer sees when they view their return:
    {
        "return_number": "RET-A1B2C3D4",
        "status": "pickup_scheduled",
        "order": { ... },
        "images": [ ... ],
        "status_history": [ ... ]
    }
    """

    order = OrderSerializer(read_only=True)
    images = ReturnImageSerializer(many=True, read_only=True)
    status_history = ReturnStatusHistorySerializer(many=True, read_only=True)

    class Meta:
        model = ReturnRequest
        fields = [
            'id', 'return_number', 'order', 'customer_id', 'customer_name',
            'customer_email', 'reason', 'reason_description', 'status',
            'refund_method', 'refund_amount', 'refund_reference',
            'pickup_address', 'pickup_pincode', 'pickup_scheduled_date',
            'pickup_completed_date', 'logistics_partner', 'tracking_number',
            'is_flagged', 'is_high_value',
            'images', 'status_history',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'return_number', 'status', 'refund_amount',
            'refund_reference', 'is_flagged', 'is_high_value',
            'created_at', 'updated_at',
        ]


# ============================================================
# RETURN LIST SERIALIZER (lightweight - for listing APIs)
# ============================================================

class ReturnRequestListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for listing returns.
    Doesn't include nested images/history (saves database queries).
    Used in: GET /api/v1/returns/ (list all returns)
    """

    order_number = serializers.CharField(source='order.order_number', read_only=True)
    product_name = serializers.CharField(source='order.product_name', read_only=True)

    class Meta:
        model = ReturnRequest
        fields = [
            'id', 'return_number', 'order_number', 'product_name',
            'customer_id', 'reason', 'status', 'refund_amount',
            'is_flagged', 'is_high_value', 'created_at',
        ]


# ============================================================
# CHECK ELIGIBILITY SERIALIZER
# ============================================================

class CheckEligibilitySerializer(serializers.Serializer):
    """
    Input for checking if an order is eligible for return.

    Customer sends: {"order_id": 123}
    Response: {"eligible": true, "return_window_days": 7, ...}
    """

    order_id = serializers.IntegerField(help_text="Order ID to check")

    def validate_order_id(self, value):
        """Check if order exists."""
        try:
            Order.objects.get(id=value)
        except Order.DoesNotExist:
            raise serializers.ValidationError("Order not found.")
        return value