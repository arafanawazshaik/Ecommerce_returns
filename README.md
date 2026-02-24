# E-Commerce Customer Returns Module

A Django REST API backend for managing customer returns in an e-commerce platform. Built with Django REST Framework, featuring webhook integrations, rule-based fraud detection, and automated testing.

## Features

- **REST APIs** - 8 endpoints for return creation, tracking, cancellation, and eligibility checks
- **Webhook Integration** - Logistics pickup and refund status updates from external partners
- **Fraud Detection** - Rule-based flagging for frequent returns, high-value items, and quick returns
- **Idempotency** - Duplicate return prevention using idempotency keys
- **Rate Limiting** - DRF throttling to prevent API abuse
- **Admin Panel** - Django admin with search, filters, and bulk actions for ops team
- **Automated Tests** - 24 unit and integration tests

## Tech Stack

- Python 3.x, Django, Django REST Framework
- SQLite (dev) / MySQL (production)
- Celery + Redis (async tasks)
- AWS S3 (image storage)

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/returns/` | Create a return request |
| GET | `/api/v1/returns/list/` | List returns with filters |
| GET | `/api/v1/returns/{id}/` | Get return details |
| GET | `/api/v1/returns/{id}/status/` | Get status timeline |
| POST | `/api/v1/returns/{id}/cancel/` | Cancel a return |
| POST | `/api/v1/returns/check-eligibility/` | Check return eligibility |
| POST | `/api/v1/returns/webhook/pickup/` | Logistics status webhook |
| POST | `/api/v1/returns/webhook/refund/` | Refund status webhook |

## Return Lifecycle
```
pending → approved → pickup_scheduled → picked_up → warehouse_received → refund_initiated → refund_completed
```

## Setup
```bash
git clone https://github.com/arafanawazshaik/Ecommerce_returns.git
cd Ecommerce_returns
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver

