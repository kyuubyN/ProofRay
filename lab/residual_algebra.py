"""Typed, identity-conserving residual circuit synthesis for D31."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
from typing import Mapping


@dataclass(frozen=True)
class AlgebraAtom:
    fact_id: str
    value: Fraction
    dimension: str

    def __post_init__(self) -> None:
        if not self.fact_id or not self.dimension:
            raise ValueError("atom identity and dimension are required")


@dataclass(frozen=True)
class Circuit:
    operator: str
    operands: tuple["Circuit", ...]
    fact_ids: tuple[str, ...]
    dimension: str
    atom_id: str | None = None

    def __post_init__(self) -> None:
        if self.operator == "atom":
            if self.atom_id is None or self.operands or self.fact_ids != (self.atom_id,):
                raise ValueError("atom circuits require exactly their own conserved identity")
        elif self.operator not in {"add", "subtract", "multiply", "divide", "maximum", "minimum"}:
            raise ValueError("unsupported residual operator")
        elif len(self.operands) != 2 or self.atom_id is not None:
            raise ValueError("residual operators are binary")
        if not self.dimension or self.fact_ids != tuple(sorted(set(self.fact_ids))):
            raise ValueError("dimension and unique sorted FactIds are required")

    def execute(self, world: Mapping[str, Fraction]) -> Fraction:
        if self.operator == "atom":
            try:
                return world[self.atom_id or ""]
            except KeyError as exc:
                raise ValueError("world is missing a circuit FactId") from exc
        left, right = (operand.execute(world) for operand in self.operands)
        if self.operator == "add":
            return left + right
        if self.operator == "subtract":
            return left - right
        if self.operator == "multiply":
            return left * right
        if self.operator == "divide":
            if right == 0:
                raise ZeroDivisionError("counterfactual world makes divisor zero")
            return left / right
        if self.operator == "maximum":
            return max(left, right)
        return min(left, right)

    def canonical(self) -> str:
        if self.operator == "atom":
            return f"${self.atom_id}:{self.dimension}"
        return f"{self.operator}({self.operands[0].canonical()},{self.operands[1].canonical()})"


def _dimension(operator: str, left: str, right: str) -> str | None:
    if operator in {"add", "subtract", "maximum", "minimum"}:
        return left if left == right else None
    if operator == "multiply":
        if left == "scalar":
            return right
        if right == "scalar":
            return left
        return "*".join(sorted((left, right)))
    if operator == "divide":
        if right == "scalar":
            return left
        if left == right:
            return "scalar"
        return f"{left}/{right}"
    return None


def _worlds(atoms: tuple[AlgebraAtom, ...], count: int) -> tuple[dict[str, Fraction], ...]:
    generated = []
    for world_index in range(count):
        world = {}
        for atom_index, atom in enumerate(atoms):
            digest = hashlib.sha256(f"D31|{world_index}|{atom_index}|{atom.fact_id}".encode()).digest()
            value = 1 + int.from_bytes(digest[:4], "big") % 101
            world[atom.fact_id] = Fraction(value)
        generated.append(world)
    return tuple(generated)


def synthesize_residual(
    atoms: tuple[AlgebraAtom, ...],
    target: Fraction,
    *,
    target_dimension: str | None = None,
    max_depth: int = 2,
    max_classes: int = 20_000,
    counterfactual_world_count: int = 5,
) -> tuple[Circuit, ...]:
    """Return minimal behavioral classes that close one training residual, or fail by budget."""
    if not atoms or len({item.fact_id for item in atoms}) != len(atoms):
        raise ValueError("unique non-empty atoms are required")
    if max_depth < 0 or max_classes < len(atoms) or counterfactual_world_count < 2:
        raise ValueError("invalid synthesis budgets")
    observed = {item.fact_id: item.value for item in atoms}
    worlds = _worlds(atoms, counterfactual_world_count)
    atom_circuits = tuple(Circuit("atom", (), (item.fact_id,), item.dimension, item.fact_id)
                          for item in atoms)
    all_circuits = list(atom_circuits)
    frontier = list(atom_circuits)
    # E-class key preserves proof identity and counterfactual behavior.  Canonical expression chooses
    # one minimal representative without claiming that observed-value equality is semantic equality.
    classes: dict[tuple, Circuit] = {}

    def admit(circuit: Circuit) -> bool:
        try:
            signature = tuple(circuit.execute(world) for world in worlds)
        except ZeroDivisionError:
            return False
        key = circuit.dimension, circuit.fact_ids, signature
        previous = classes.get(key)
        if previous is None or circuit.canonical() < previous.canonical():
            classes[key] = circuit
            return previous is None
        return False

    for circuit in atom_circuits:
        admit(circuit)

    commutative = {"add", "multiply", "maximum", "minimum"}
    for _depth in range(1, max_depth + 1):
        next_frontier = []
        population = tuple(all_circuits)
        for left in frontier:
            for right in population:
                if set(left.fact_ids) & set(right.fact_ids):
                    continue
                for operator in ("add", "subtract", "multiply", "divide", "maximum", "minimum"):
                    if operator in commutative and right.canonical() < left.canonical():
                        continue
                    dimension = _dimension(operator, left.dimension, right.dimension)
                    if dimension is None:
                        continue
                    fact_ids = tuple(sorted(left.fact_ids + right.fact_ids))
                    circuit = Circuit(operator, (left, right), fact_ids, dimension)
                    if admit(circuit):
                        next_frontier.append(circuit)
                        if len(classes) > max_classes:
                            raise RuntimeError("residual e-graph exceeds the frozen class budget")
        all_circuits.extend(next_frontier)
        frontier = next_frontier
        if not frontier:
            break

    closed = [circuit for circuit in classes.values()
              if (target_dimension is None or circuit.dimension == target_dimension)
              and circuit.execute(observed) == target]
    if not closed:
        return ()
    minimum_atoms = min(len(circuit.fact_ids) for circuit in closed)
    return tuple(sorted((circuit for circuit in closed if len(circuit.fact_ids) == minimum_atoms),
                        key=Circuit.canonical))
