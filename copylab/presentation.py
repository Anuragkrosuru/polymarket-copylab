"""Keep quantities in accounting, but omit them from public report views."""


def without_quantities(value):
    if isinstance(value, dict):
        return {k:without_quantities(v) for k,v in value.items()
                if k not in ('shares','shares_each','copied_exit_shares_total')}
    if isinstance(value, list):
        return [without_quantities(v) for v in value]
    return value
