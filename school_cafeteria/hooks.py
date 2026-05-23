import logging

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """Configure Student Wallet payment method with journal and proper settings."""
    company = env.company

    # Find or create a cash journal for the Student Wallet
    journal = env['account.journal'].search([
        ('code', '=', 'STUW'),
        ('company_id', '=', company.id),
    ], limit=1)

    if not journal:
        # Find an existing cash account to use as default
        default_cash_account = env['account.account'].with_context(lang='en_US').search([
            ('account_type', '=', 'asset_cash'),
            ('company_ids', 'in', company.root_id.id),
        ], limit=1)

        journal_vals = {
            'name': 'Student Wallet',
            'code': 'STUW',
            'type': 'cash',
            'company_id': company.id,
        }
        if default_cash_account:
            journal_vals['default_account_id'] = default_cash_account.id

        journal = env['account.journal'].create(journal_vals)
        _logger.info('Created Student Wallet cash journal: %s', journal.name)

    # Find the Student Wallet payment method and configure it
    payment_method = env.ref('school_cafeteria.payment_method_student_wallet', raise_if_not_found=False)
    if payment_method:
        payment_method.write({
            'journal_id': journal.id,
            'split_transactions': True,
        })
        _logger.info('Configured Student Wallet payment method with journal and split_transactions')
    else:
        _logger.warning('Student Wallet payment method not found')
