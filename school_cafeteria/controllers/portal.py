# -*- coding: utf-8 -*-
from odoo import fields, http
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager


class CafeteriaPortal(CustomerPortal):
    """
    Parent portal: /my/cafeteria
    Parents log in via Odoo's standard portal and see:
    - Children's balances
    - Recent transactions (last 50)
    - Recharge request form
    - Ability to update spending limits (pending admin approval)
    """

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        partner = request.env.user.partner_id
        student_count = request.env['school.student'].sudo().search_count([
            ('parent_id', '=', partner.id),
        ])
        values['cafeteria_count'] = student_count
        return values

    @http.route(['/my/cafeteria'], type='http', auth='user', website=True)
    def portal_cafeteria(self, **kwargs):
        partner = request.env.user.partner_id

        students = request.env['school.student'].sudo().search([
            ('parent_id', '=', partner.id),
            ('active', '=', True),
        ])

        # Last 50 transactions across all children
        transactions = request.env['school.wallet.transaction'].sudo().search([
            ('student_id', 'in', students.ids),
            ('state', '=', 'done'),
        ], order='date desc', limit=50)

        return request.render('school_cafeteria.portal_cafeteria_page', {
            'students': students,
            'transactions': transactions,
            'page_name': 'cafeteria',
        })

    @http.route(['/my/cafeteria/recharge'], type='http', auth='user', website=True, methods=['GET', 'POST'])
    def portal_recharge(self, **kwargs):
        partner = request.env.user.partner_id

        students = request.env['school.student'].sudo().search([
            ('parent_id', '=', partner.id),
            ('active', '=', True),
        ])
        plans = request.env['cafeteria.plan'].sudo().search([('active', '=', True)])

        if request.httprequest.method == 'POST':
            student_id = int(kwargs.get('student_id') or 0)
            amount = float(kwargs.get('amount') or 0)
            plan_id_raw = kwargs.get('plan_id', '')
            plan_id = int(plan_id_raw) if plan_id_raw else False
            note = kwargs.get('note', '')

            student = request.env['school.student'].sudo().browse(student_id)
            if student and student.parent_id == partner:
                plan = request.env['cafeteria.plan'].sudo().browse(plan_id) if plan_id else None
                vals = {
                    'student_id': student_id,
                    'payment_amount': plan.price if plan else amount,
                    'credit_amount': plan.credit_amount if plan else amount,
                    'recharge_type': 'plan' if plan else 'manual',
                    'plan_id': plan_id or False,
                    'note': note,
                }
                request.env['school.recharge.request'].sudo().create(vals)
                return request.redirect('/my/cafeteria?recharge_sent=1')

        return request.render('school_cafeteria.portal_recharge_page', {
            'students': students,
            'plans': plans,
            'page_name': 'cafeteria',
        })
