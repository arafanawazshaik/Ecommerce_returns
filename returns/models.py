"""
Returns Module - Database Models

These models represent the database tables for the Customer Returns system.
Think of each class as a table in MySQL/SQLite.

TABLES:
1. Order         → The original order (we need this to validate returns)
2. ReturnRequest → The actual return request created by customer
3. ReturnImage   → Photos uploaded by customer as proof
4. ReturnStatusHistory → Track every status change (audit trail)
5. FraudFlag     → Flag suspicious return patterns
"""

import uuid
from django.db import models
from django.conf import settings


# ============================================================
# ORDER MODEL
# ============================================================
# In real Flipkart, this would be in a separate Order service.
# We create a simplified version here so we can validate returns against it.

class Order(models.Model):
    """
    Represents a customer's original order.
    We need order data to check: Was it delivered? When? What category? How much?
    """

    CATEGORY_CHOICES = [
        ('electronics', 'Electronics'),
        ('fashion', 'Fashion'),
        ('home', 'Home & Kitchen'),
        ('books', 'Books'),
        ('grocery', 'Grocery'),
        ('beauty', 'Beauty'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('shipped', 'Shipped'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled'),
    ]

    # order_id is auto-generated unique ID (like Flipkart's OD1234567890)
    order_number = models.CharField(max_length=50, unique=True, db_index=True)
    customer_id = models.IntegerField(db_index=True)       # Who placed the order
    customer_name = models.CharField(max_length=200)
    customer_email = models.EmailField()
    customer_phone = models.CharField(max_length=15, blank=True)

    # Product details
    product_name = models.CharField(max_length=500)
    product_sku = models.CharField(max_length=100)          # Stock Keeping Unit
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)

    # Order status and dates
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    ordered_at = models.DateTimeField()
    delivered_at = models.DateTimeField(null=True, blank=True)  # NULL if not yet delivered

    # Payment info (needed for refund processing)
    payment_method = models.CharField(max_length=50)   # 'upi', 'credit_card', 'cod', 'wallet'
    payment_reference = models.CharField(max_length=200, blank=True)

    # Shipping address
    shipping_address = models.TextField()
    shipping_pincode = models.CharField(max_length=10)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'orders'
        ordering = ['-ordered_at']
        indexes = [
            models.Index(fields=['customer_id', 'status']),
            models.Index(fields=['order_number']),
        ]

    def __str__(self):
        return f"Order {self.order_number} - {self.product_name}"


# ============================================================
# RETURN REQUEST MODEL
# ============================================================
# This is the MAIN table - every return request lives here

class ReturnRequest(models.Model):
    """
    The core model. When a customer says "I want to return this",
    a row is created in this table.
    """

    RETURN_REASON_CHOICES = [
        ('defective', 'Product is Defective/Damaged'),
        ('wrong_item', 'Wrong Item Delivered'),
        ('not_as_described', 'Product Not as Described'),
        ('size_issue', 'Size/Fit Issue'),
        ('quality_issue', 'Quality Not as Expected'),
        ('changed_mind', 'Changed My Mind'),
        ('late_delivery', 'Delivered Too Late'),
        ('missing_parts', 'Missing Parts/Accessories'),
        ('other', 'Other'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending Review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('pickup_scheduled', 'Pickup Scheduled'),
        ('picked_up', 'Picked Up'),
        ('warehouse_received', 'Received at Warehouse'),
        ('quality_check', 'Quality Check in Progress'),
        ('refund_initiated', 'Refund Initiated'),
        ('refund_completed', 'Refund Completed'),
        ('cancelled', 'Cancelled by Customer'),
        ('closed', 'Closed'),
    ]

    REFUND_METHOD_CHOICES = [
        ('original', 'Original Payment Method'),
        ('wallet', 'Store Wallet/Credit'),
        ('bank_transfer', 'Bank Transfer'),
    ]

    # Unique return ID (like RET-xxxxxxxx)
    return_number = models.CharField(max_length=50, unique=True, db_index=True)

    # Link to the original order
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='returns')

    # Customer info (denormalized for quick access without joining Order table)
    customer_id = models.IntegerField(db_index=True)
    customer_name = models.CharField(max_length=200)
    customer_email = models.EmailField()

    # Return details
    reason = models.CharField(max_length=50, choices=RETURN_REASON_CHOICES)
    reason_description = models.TextField(blank=True)  # Customer's detailed explanation
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='pending')

    # Refund details
    refund_method = models.CharField(max_length=20, choices=REFUND_METHOD_CHOICES, default='original')
    refund_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    refund_reference = models.CharField(max_length=200, blank=True)  # Transaction ID from refund service

    # Pickup details (filled when logistics partner is assigned)
    pickup_address = models.TextField(blank=True)
    pickup_pincode = models.CharField(max_length=10, blank=True)
    pickup_scheduled_date = models.DateTimeField(null=True, blank=True)
    pickup_completed_date = models.DateTimeField(null=True, blank=True)
    logistics_partner = models.CharField(max_length=100, blank=True)  # e.g., 'Delhivery', 'Ecom Express'
    tracking_number = models.CharField(max_length=200, blank=True)

    # Idempotency key to prevent duplicate submissions
    idempotency_key = models.CharField(max_length=100, unique=True, null=True, blank=True)

    # Flags
    is_flagged = models.BooleanField(default=False)     # Flagged for fraud review
    is_high_value = models.BooleanField(default=False)  # Amount > Rs.10,000

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'return_requests'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['customer_id', 'status']),
            models.Index(fields=['return_number']),
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['is_flagged']),
        ]

    def __str__(self):
        return f"Return {self.return_number} - {self.order.order_number}"

    def generate_return_number(self):
        """Generate unique return number like RET-a1b2c3d4"""
        return f"RET-{uuid.uuid4().hex[:8].upper()}"

    def save(self, *args, **kwargs):
        """Auto-generate return_number on first save"""
        if not self.return_number:
            self.return_number = self.generate_return_number()
        super().save(*args, **kwargs)


# ============================================================
# RETURN IMAGE MODEL
# ============================================================
# Customers upload photos of damaged/wrong products

class ReturnImage(models.Model):
    """
    Stores references to images uploaded by customers.
    Actual images go to S3, we just store the S3 key/URL here.
    """

    return_request = models.ForeignKey(
        ReturnRequest,
        on_delete=models.CASCADE,
        related_name='images'
    )
    image_key = models.CharField(max_length=500)        # S3 object key
    image_url = models.URLField(max_length=1000, blank=True)  # Pre-signed URL (temporary)
    file_name = models.CharField(max_length=255)
    file_size = models.IntegerField(default=0)          # Size in bytes
    content_type = models.CharField(max_length=50, default='image/jpeg')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'return_images'
        ordering = ['uploaded_at']

    def __str__(self):
        return f"Image for {self.return_request.return_number} - {self.file_name}"


# ============================================================
# RETURN STATUS HISTORY MODEL
# ============================================================
# Every status change is recorded here (audit trail)
# This is how customers see "Pickup scheduled → Picked up → Refund initiated"

class ReturnStatusHistory(models.Model):
    """
    Tracks every status change for a return request.
    This provides the timeline view customers see in the app.

    Example timeline:
        2024-01-15 10:00 → pending       (Customer submitted return)
        2024-01-15 10:05 → approved      (Auto-approved by eligibility check)
        2024-01-16 09:00 → pickup_scheduled (Logistics partner assigned)
        2024-01-17 14:30 → picked_up     (Webhook from logistics partner)
        2024-01-19 11:00 → warehouse_received
        2024-01-19 15:00 → refund_initiated
        2024-01-20 10:00 → refund_completed
    """

    return_request = models.ForeignKey(
        ReturnRequest,
        on_delete=models.CASCADE,
        related_name='status_history'
    )
    from_status = models.CharField(max_length=30, blank=True)    # Previous status
    to_status = models.CharField(max_length=30)                   # New status
    changed_by = models.CharField(max_length=100, default='system')  # 'system', 'customer', 'admin', 'webhook'
    comment = models.TextField(blank=True)                        # Optional note
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'return_status_history'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['return_request', 'created_at']),
        ]

    def __str__(self):
        return f"{self.return_request.return_number}: {self.from_status} → {self.to_status}"


# ============================================================
# FRAUD FLAG MODEL
# ============================================================
# Rule-based flags for suspicious return activity

class FraudFlag(models.Model):
    """
    When our rule-based checks detect suspicious patterns,
    a flag is created here for the ops team to review.

    Rules (implemented in business logic layer):
    - Customer has > 10 returns in 30 days
    - Return amount > Rs.10,000
    - Return requested within 1 hour of delivery
    - Same address returning items on multiple accounts
    """

    FLAG_TYPE_CHOICES = [
        ('frequent_returns', 'Too Many Returns in 30 Days'),
        ('high_value', 'High Value Return'),
        ('quick_return', 'Return Within 1 Hour of Delivery'),
        ('address_pattern', 'Suspicious Address Pattern'),
        ('category_abuse', 'Repeated Returns in Same Category'),
    ]

    STATUS_CHOICES = [
        ('open', 'Open - Needs Review'),
        ('investigating', 'Under Investigation'),
        ('cleared', 'Cleared - No Fraud'),
        ('confirmed', 'Fraud Confirmed'),
    ]

    return_request = models.ForeignKey(
        ReturnRequest,
        on_delete=models.CASCADE,
        related_name='fraud_flags'
    )
    customer_id = models.IntegerField(db_index=True)
    flag_type = models.CharField(max_length=50, choices=FLAG_TYPE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    description = models.TextField()           # Details about why this was flagged
    reviewed_by = models.CharField(max_length=100, blank=True)  # Admin who reviewed
    review_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'fraud_flags'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['customer_id', 'flag_type']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"Flag: {self.flag_type} - Customer {self.customer_id}"
