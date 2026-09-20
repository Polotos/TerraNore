from __future__ import annotations

from collections.abc import Callable, Iterable

from .model import Shipment, TradeOrder


def resolve_orders(
    orders: Iterable[TradeOrder],
    distance: Callable[[str, str], float] | None = None,
    route_exists: Callable[[str, str], bool] | None = None,
) -> tuple[Shipment, ...]:
    """Match bids centrally using price, distance and stable identifiers."""
    distance = distance or (lambda _a, _b: 0.0)
    route_exists = route_exists or (lambda _a, _b: True)
    buys = sorted((o for o in orders if o.quantity > 0), key=lambda o: (-o.price, o.region_id, o.sequence))
    sells = sorted((o for o in orders if o.quantity < 0), key=lambda o: (o.price, o.region_id, o.sequence))
    remaining = {id(order): abs(order.quantity) for order in (*buys, *sells)}
    shipments: list[Shipment] = []
    sequence = 0
    for buyer in buys:
        candidates = sorted(
            (seller for seller in sells if seller.price <= buyer.price and route_exists(seller.region_id, buyer.region_id)),
            key=lambda seller: (seller.price, distance(seller.region_id, buyer.region_id), seller.region_id, seller.sequence),
        )
        for seller in candidates:
            quantity = min(remaining[id(buyer)], remaining[id(seller)])
            if quantity <= 0:
                continue
            shipments.append(Shipment(seller.region_id, buyer.region_id, quantity, seller.price, sequence))
            sequence += 1
            remaining[id(buyer)] -= quantity
            remaining[id(seller)] -= quantity
            if remaining[id(buyer)] <= 0:
                break
    return tuple(shipments)
