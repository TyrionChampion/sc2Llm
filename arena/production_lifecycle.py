"""Production intermediates whose raw removal is not evidence of an enemy kill."""
from arena.tech_tree import UNIT_NAMES

PRODUCTION_INTERMEDIATES={uid for uid,name in UNIT_NAMES.items()
    if name=='Larva' or name.endswith('Egg') or 'Cocoon' in name}
REMOVAL_NOTE=('Native dead_units removal. Hatching, morphing, cancellation and combat loss are not '
    'distinguished by this event. Do not attribute a kill or assume normal hatching without more evidence.')
