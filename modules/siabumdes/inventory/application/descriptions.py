"""Consistent user-facing descriptions; references remain machine identifiers."""


def inventory_description(action, *, partner=None, invoice=None, product=None, quantity=None, note=None):
    parts = [action]
    if partner:
        parts.append(partner.strip())
    if invoice:
        parts.append(invoice.strip())
    if product:
        parts.append(f"{product.strip()} ×{quantity}" if quantity is not None else product.strip())
    if note and note.strip():
        parts.append(note.strip())
    return " · ".join(parts)
