import unittest

from src.simulation.lod import (
    AggregateState, Construction, LOD, LODSimulator, Shipment, SimulationNode,
    aggregate, balance_of, change_lod,
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

    def test_detailed_child_is_excluded_from_coarse_parent_tick(self):
        detailed = SimulationNode("detailed", state(10, 50, 70), LOD.ENTERPRISE)
        detailed.state.consumption["food"] = 2
        detailed.set_lod(LOD.ENTERPRISE)
        coarse = SimulationNode("coarse", state(10, 50, 30), LOD.ENTERPRISE)
        coarse.state.consumption["food"] = 2
        parent = SimulationNode(
            "system",
            aggregate([detailed, coarse]),
            children=[detailed, coarse],
        )

        LODSimulator().advance(parent, 1, LOD.AGGREGATE)

        self.assertEqual(parent.state.production["food"], 20)
        self.assertEqual(parent.state.consumption["food"], 4)
        self.assertEqual(parent.state.population, 20)
        self.assertEqual(parent.state.stocks["food"], 116)
        self.assertEqual(parent.state.money, 100)

    def test_coarse_ticks_are_distributed_before_detail_is_reactivated(self):
        first = SimulationNode("a", state(20, 80, 40, 0.5), LOD.ENTERPRISE)
        second = SimulationNode("b", state(30, 120, 60, 1.0), LOD.ENTERPRISE)
        first.state.capital, second.state.capital = 1, 3
        first.state.production_capacity["food"] = 5
        second.state.production_capacity["food"] = 15
        first.state.consumption["food"] = second.state.consumption["food"] = 2
        parent = SimulationNode("world", children=[second, first], native_lod=LOD.ENTERPRISE)

        change_lod(parent, LOD.AGGREGATE)
        saved_food = [first.state.stocks["food"], second.state.stocks["food"]]
        LODSimulator().advance(parent, 3, LOD.AGGREGATE)
        parent.state.population += 10
        parent.state.available_labour += 10
        parent.state.money += 80
        parent.state.construction.append(Construction("new-orchard", 4, {"food": 6}))
        coarse_balance = balance_of([parent])

        change_lod(parent, LOD.ENTERPRISE)

        self.assertTrue(all(child.active for child in parent.children))
        self.assertEqual([first.state.population, second.state.population], [24, 36])
        self.assertEqual([first.state.money, second.state.money], [60, 120])
        self.assertNotEqual([first.state.stocks["food"], second.state.stocks["food"]], saved_food)
        self.assertEqual(sum(len(child.state.construction) for child in parent.children), 1)
        buffered_nodes = parent.children + [
            SimulationNode("buffer", parent.reconciliation_buffer)
        ]
        self.assertEqual(coarse_balance, balance_of(buffered_nodes))


if __name__ == "__main__":
    unittest.main()
