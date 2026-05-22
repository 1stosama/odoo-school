# School Cafeteria POS

**Odoo 19 Module** — Student meal management, cafeteria POS integration, spending control & parent notifications.

## Features

- **Student Wallet** — Prepaid cafeteria balance for each student, auto-deducted at POS
- **Card Scan Integration** — Scan student barcode/QR cards directly in Odoo POS with instant student lookup
- **Meal Plans** — Monthly, semester, or annual subscription plans with bonus credits
- **Spending Controls** — Daily limits, forbidden products, time windows, category restrictions
- **Parent Notifications** — WhatsApp / SMS / email alerts on every purchase
- **Parent Portal** — `/my/cafeteria` for balance, history, and recharge requests
- **Student ID Cards** — Printable cards with barcode
- **Daily Reports** — Sales analysis and transaction audit trail
- **Low Balance Alerts** — Automatic notifications via scheduled cron

## Requirements

- Odoo 19.0
- point_of_sale, portal, mail modules

## Installation

1. Copy the `school_cafeteria` folder to your Odoo addons directory
2. Update the apps list (Apps → Update Apps List)
3. Install "School Cafeteria POS"

## Configuration

1. **Create Students** — School Cafeteria → Operations → Students
2. **Configure Meal Plans** — School Cafeteria → Configuration → Meal Plans
3. **POS Payment Method** — "Student Wallet" is auto-created on install
4. **Parent Portal** — Give portal access to parent contacts linked to students
5. **Notifications** — Set `cafeteria.notification.service.url` in System Parameters

## Pricing

- **$199 USD** — License: **OPL-1** (Odoo Proprietary License v1.0)
- Available on [Odoo Apps Store](https://apps.odoo.com/apps/modules/19.0/school_cafeteria/)

## Support

For support inquiries, contact appmob82@gmail.com.

## Author

**Osama Ahmed** — MindPower Software
