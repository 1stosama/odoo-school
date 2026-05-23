# -*- coding: utf-8 -*-
{
    'name': 'School Cafeteria POS',
    'version': '19.0.1.0.0',
    'category': 'Education',
    'summary': 'Student meal management, cafeteria POS, spending control & parent notifications',
    'author': 'Osama Ahmed',
    'license': 'OPL-1',
    'price': 199.00,
    'currency': 'USD',
    'depends': [
        'base',
        'mail',
        'point_of_sale',
        'stock',
        'portal',
        'product',
        'web',
        'account',
    ],
    'data': [
        # 1. Groups — no model deps
        'security/groups.xml',

        # 2. Sequences & system params — no model deps
        'data/sequence.xml',
        'data/pos_payment_method.xml',

        # 3. Wizards (actions referenced by views, must load first)
        'wizard/recharge_wizard_views.xml',

        # 4. Views — depends on models being registered by Python
        'views/school_transaction_views.xml',
        'views/cafeteria_plan_views.xml',
        'views/school_student_views.xml',
        'views/school_wallet_views.xml',
        'views/school_recharge_views.xml',
        'views/res_partner_views.xml',
        'views/portal_templates.xml',
        'views/cafeteria_menu.xml',

        # 5. Reports
        'report/student_card_report.xml',
        'report/daily_sales_report.xml',

        # 6. Cron jobs — refs model_school_student, must be after models registered
        'data/cron.xml',

        # 7. Access rules — refs all custom models, must be absolutely last
        'security/ir_model_access.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'school_cafeteria/static/src/scss/cafeteria_form_overrides.scss',
        ],
        'point_of_sale._assets_pos': [
            'school_cafeteria/static/src/js/cafeteria_pos.js',
        ],
    },
    'images': [
        'static/description/banner.png',
        'static/description/screenshot_03_student_list.png',
        'static/description/screenshot_04_student_form.png',
        'static/description/screenshot_07_pos_product_screen.png',
        'static/description/screenshot_08_pos_student_card.png',
        'static/description/screenshot_11_portal.png',
    ],
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': True,
    'auto_install': False,
}
