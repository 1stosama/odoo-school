# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class CafeteriaPlan(models.Model):
    """
    Meal subscription plans sold to parents.
    Parent PAYS `price`, student RECEIVES `credit_amount` in their wallet.
    The difference (bonus) is the plan's value proposition.

    Example: Annual Gold Plan
        price = 4,500 EGP  (what parent pays to school)
        credit_amount = 5,400 EGP  (what gets loaded into wallet = +20% bonus)
    """
    _name = 'cafeteria.plan'
    _description = 'Cafeteria Meal Plan'
    _order = 'duration_type, price'

    name = fields.Char(
        string='Plan Name',
        required=True,
        help='e.g. "Annual Gold Plan", "Monthly Basic"',
    )
    active = fields.Boolean(default=True)

    duration_type = fields.Selection(
        selection=[
            ('payg', 'Pay As You Go'),
            ('monthly', 'Monthly'),
            ('semester', 'Semester (5 months)'),
            ('annual', 'Annual'),
        ],
        string='Duration',
        required=True,
        default='monthly',
    )

    # --- Financial fields ---
    price = fields.Float(
        string='Price (EGP)',
        required=True,
        help='Amount parent pays to the school.',
    )
    credit_amount = fields.Float(
        string='Credits Loaded (EGP)',
        required=True,
        help='Amount credited to student wallet. Should be >= price to give bonus.',
    )
    bonus_percent = fields.Float(
        string='Bonus %',
        compute='_compute_bonus_percent',
        store=True,
    )

    # --- Spending rules ---
    daily_limit = fields.Float(
        string='Daily Spending Limit (EGP)',
        default=0.0,
        help='0 = no limit enforced by plan (student rule still applies).',
    )
    allowed_category_ids = fields.Many2many(
        comodel_name='product.category',
        string='Allowed Product Categories',
        help='Leave empty = all categories allowed. Set to restrict to healthy meals only, etc.',
    )

    # --- Description ---
    description = fields.Text(
        string='Plan Description',
        help='Shown to parents in the portal.',
    )

    # --- Stats ---
    student_count = fields.Integer(
        string='Active Students',
        compute='_compute_student_count',
    )

    # ------------------------------------------------------------------ #
    #  Computed                                                            #
    # ------------------------------------------------------------------ #

    @api.depends('price', 'credit_amount')
    def _compute_bonus_percent(self):
        for plan in self:
            if plan.price > 0:
                plan.bonus_percent = ((plan.credit_amount - plan.price) / plan.price) * 100
            else:
                plan.bonus_percent = 0.0

    def _compute_student_count(self):
        for plan in self:
            plan.student_count = self.env['school.student'].search_count([
                ('plan_id', '=', plan.id),
                ('plan_expiry', '>=', fields.Date.today()),
            ])

    # ------------------------------------------------------------------ #
    #  Constraints                                                         #
    # ------------------------------------------------------------------ #

    @api.constrains('price', 'credit_amount')
    def _check_credit_not_less_than_price(self):
        for plan in self:
            if plan.credit_amount < plan.price:
                raise ValidationError(
                    'Credits loaded cannot be less than price. '
                    'The plan must give equal or more value than what is paid.'
                )

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def action_view_students(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Students - {self.name}',
            'res_model': 'school.student',
            'view_mode': 'list,form',
            'domain': [('plan_id', '=', self.id)],
            'context': {'default_plan_id': self.id},
        }

    def get_duration_days(self):
        """Return number of days this plan covers."""
        self.ensure_one()
        mapping = {
            'payg': 0,
            'monthly': 30,
            'semester': 150,
            'annual': 365,
        }
        return mapping.get(self.duration_type, 30)
