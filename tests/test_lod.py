import unittest

from src.simulation.lod import (
    AggregateState, Construction, LOD, LODSimulator, Shipment, SimulationNode,
    balance_of, change_lod,
)


def state(population, food, money, infrastructure=1.0):
    return AggregateState(
        population=population,
        available_labour=population,
        production_capacity={"food": 10},
        stocks={"food": food},
        stock_capacity={"food": 1000},
        money=money,
        infrastructure=infrastructure,
    )


class LODTests(unittest.TestCase):
    def test_coarse_long_tick_uses_monthly_sequence(self):
        aggregate = state(10, 0, 0)
        aggregate.production_capacity["food"] = 0
        aggregate.consumption["food"] = 10
        aggregate.shipments.append(Shipment("food", 30, 2))
        node = SimulationNode("colony", aggregate)

        LODSimulator().advance(node, 3, LOD.AGGREGATE)

        self.assertEqual(aggregate.stocks["food"], 10)
        self.assertEqual(aggregate.deficit["food"], 10)
        self.assertEqual(aggregate.provision["food"], 1)

    def test_transition_retains_children_and_conserved_balances(self):
        first = SimulationNode("a", state(20, 30, 40, 0.5), LOD.ENTERPRISE)
        second = SimulationNode("b", state(30, 50, 60, 1.0), LOD.ENTERPRISE)
        first.state.shipments.append(Shipment("ore", 7, 1, 3))
        second.state.construction.append(Construction("mine", 2, {"ore": 3}, {"ore": 4}))
        parent = SimulationNode("world", children=[first, second], native_lod=LOD.ENTERPRISE)
        expected = balance_of(parent.children)

        change_lod(parent, LOD.AGGREGATE)
        self.assertFalse(first.active)
        self.assertEqual(expected, balance_of([parent]))
        change_lod(parent, LOD.ENTERPRISE)

        self.assertTrue(first.active)
        self.assertIs(parent.children[0], first)
        self.assertEqual(expected, balance_of(parent.children))

    def test_manual_child_override_runs_below_coarse_parent(self):
        child_state = state(10, 0, 0)
        child_state.consumption["food"] = 2
        child = SimulationNode("factory", child_state, LOD.ENTERPRISE)
        child.set_lod(LOD.ENTERPRISE)
        parent = SimulationNode("system", state(0, 0, 0), children=[child])

        LODSimulator().advance(parent, 1, LOD.AGGREGATE)

        self.assertGreater(child.state.production["food"], 0)


if __name__ == "__main__":
    unittest.main()
