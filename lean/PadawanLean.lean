import Mathlib

/-!
Padawan's pinned Lean environment. Runtime tasks are compiled in isolated temporary files and may
import only modules admitted by the Python environment contract.
-/

namespace PadawanLean

theorem environmentSmoke (a b : Nat) : a + b = b + a := by
  omega

end PadawanLean
