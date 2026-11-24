"""Describe the pre-action image explicitly; retain all original video prompts."""
from pathlib import Path
import hashlib
import json
import shutil
import time

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / 'runs/phygen_i2v_qwen21_20261111'
STYLE = ('A single photorealistic live-action camera frame with realistic materials, '
         'natural lighting and a coherent photographic background. '
         'Show the whole interaction area and the required objects clearly. '
         'No diagram, caption, collage, montage or before-and-after panels. ')


def descriptions():
    rows = {}
    def group(start, objects, template):
        for offset, obj in enumerate(objects):
            rows[start + offset] = template.format(obj=obj)
    group(1, ['a drinking cup filled with water', 'a drinking cup filled with oil',
              'an open bottle filled with juice', 'an open squeeze container filled with honey',
              'an opened soda can filled with soda'],
          'Inside a realistic space-station cabin, an astronaut hand holds {obj} upright. '
          'The liquid is still entirely inside this one container. Its opening faces an empty '
          'region of cabin air, ready for release out of the container. No receiving vessel, '
          'pouring stream, splashes or released liquid are visible yet.')
    for i, (obj, container) in enumerate(zip(
            ['a small piece of iron', 'a small stone', 'a tennis ball', 'a small wooden block', 'a glass ball'],
            ['a transparent water tank', 'a swimming pool', 'a bucket', 'a bowl', 'a bathtub']), 6):
        rows[i] = (f'A hand lightly holds {obj} immediately above the calm water surface in '
                   f'{container} filled with water. A small air gap is visible. The object '
                   'has not touched the water. Both the object and the water surface are in view.')
    group(11, ['a soaked sponge', 'a soaked soft towel', 'a fully inflated air balloon',
               'an open toothpaste tube', 'a soaked soft cloth'],
          'Close view of two hands lightly positioned around {obj}, before applying pressure. '
          'It retains its full uncompressed shape. No squeezing, escaping fluid or emerging '
          'toothpaste is visible yet. The complete object is visible against a simple realistic background.')
    group(16, ['an empty thin plastic bottle', 'an empty aluminum can', 'an empty plastic ball',
               'an empty thin aluminum can', 'an inflated thin balloon'],
          '{obj} rests intact in its normal expanded shape. A small air-extraction tube is '
          'attached at its sealed opening, ready but inactive. It has no dents, collapse or '
          'deformation. A realistic tabletop and the whole object are visible.')
    for i, (tool, obj) in enumerate(zip(
            ['a needle', 'a pointed pencil', 'a fork', 'a pin', 'a pointed pencil'],
            ['a fully inflated balloon', 'a tightly stretched rubber sheet',
             'a taut piece of plastic wrap', 'an intact water-filled balloon', 'a fully inflated balloon']), 21):
        rows[i] = (f'A hand holds {tool} with its tip a small visible distance above {obj}, '
                   'ready to press against it. The target is intact, smooth and unpunctured. '
                   'No tip penetration or material rupture has occurred.')
    group(26, ['a colorful elastic rubber ball', 'an orange basketball', 'a yellow tennis ball',
               'a football', 'a colorful inflatable beach ball'],
          'A hand holds {obj} at waist height above a solid level floor, before throwing '
          'it downward. The complete ball, hand and a wide clear area of floor below it '
          'are visible. The ball has its natural undeformed shape and has not been released.')
    for i, (tool, surface) in enumerate(zip(
            ['white chalk', 'a blue marker', 'a red crayon', 'a yellow highlighter', 'a black pen'],
            ['a clean rough dark blackboard', 'a clean smooth whiteboard', 'a blank white sheet of paper',
             'a blank brown sheet of cardboard', 'a blank white notebook page']), 31):
        rows[i] = f'A hand holds {tool} just above {surface}, ready to make the first mark. The visible writing area is blank.'
    group(36, ['a textured lotus leaf', 'a smooth glass surface', 'a waxed apple',
               'a ceramic vase', 'a book surface'],
          'Macro photograph of one intact glistening dewdrop resting on {obj}. '
          'The surface is gently inclined with clear empty room below the drop. '
          'The drop has not begun sliding and leaves no trail.')
    # These prompts already describe an ongoing or static optical setup. Its
    # initial still can retain that setup without inventing a preceding event.
    source = json.loads((RUN / 'paired_prompts.json').read_text())
    for i in range(41, 61):
        rows[i] = source[i - 1]['video_prompt'].strip() + ' Capture one still frame of this scene at its starting position.'
    for i, (obj, surface) in enumerate(zip(
            ['a wooden block', 'a football', 'a toy car', 'a basketball'],
            ['an upright mirror', 'a vertical glass window', 'an upright glass pane', 'a shiny metallic panel']), 61):
        rows[i] = (f'{obj} rests on a level surface in front of {surface}, with a clear '
                   'gap of roughly one object width between them. Show both the whole object '
                   'and the reflective surface. The object is stationary, before approaching the surface.')
    rows[65] = ('A rubber duck is positioned just above a calm clear water surface, with '
                'a visible air gap. Both the duck and the water beneath it are in frame, '
                'before the duck moves closer to the water.')
    group(66, ['an open book', 'a wristwatch face', 'a leaf', 'a coin', 'flower petals'],
          'A hand holds a magnifying glass several centimeters above {obj}, before moving '
          'the lens closer. The lens, handle and entire target are clearly visible in a '
          'realistic close-up. Everything is stationary.')
    group(71, ['a clear plastic straw', 'a silver spoon', 'a wooden pencil', 'a metal fork', 'a plastic ruler'],
          'A hand holds {obj} angled just above a clear glass of still transparent water. '
          'Its lower end remains above the water surface with a visible gap. Nothing is '
          'submerged yet. The complete glass and the object are visible.')
    for i in range(76, 91):
        rows[i] = source[i - 1]['video_prompt'].strip() + ' A single still photograph of this described optical scene.'
    group(91, ['water', 'juice', 'milk', 'oil', 'beer'],
          'A clear drinking glass containing entirely liquid {obj} sits on a clean tabletop '
          'at room temperature, before cooling begins. The glass is clear and dry; there is '
          'no ice, frost, frozen crust or solid material in the liquid. Natural indoor lighting.')
    group(96, ['a solid piece of ice', 'an intact unlit wax candle', 'a solid piece of butter',
               'a firm scoop of ice cream', 'a solid piece of wax'],
          '{obj} rests on a clean tray before heating begins. It retains its original solid '
          'shape, with no melting, dripping or surrounding puddle. Close photographic view.')
    group(101, ['a clean dry cool glass surface in a humid room', 'a clean warm car window in cold fog',
                'a clean dry cool metal pot lid in a steamy kitchen',
                'a clean dry cool plastic sheet in a misty greenhouse',
                'a clean dry cool plastic sheet in a humid greenhouse'],
          'Close photograph of {obj}. Moist air is nearby but the target surface is still '
          'clear and free of droplets, films or frost, immediately before exposure.')
    group(106, ['water in an open pot', 'milk in an open kettle', 'soup in an open saucepan',
                'tea in an open teapot', 'oil in an open cauldron'],
          '{obj} rests on a kitchen heat source before heating begins. The fluid surface is '
          'still and visible. No boiling bubbles, steam or visible transformation yet.')
    group(111, ['a cold glass surface in a humid room', 'a cold kitchen window near steam',
                'a cold metal surface in a misty rainforest', 'a cold car windshield in mountain fog',
                'a cold plastic bottle in a damp refrigerated room'],
          'A close photographic view of {obj}, at the beginning of moisture exposure. '
          'Its visible surface is clean, dry and clear, without water droplets, films or frost yet.')
    group(116, ['a solid piece of dry ice', 'solid white naphthalene pellets', 'dark solid iodine crystals',
                'a solid white piece of camphor', 'solid white arsenic trioxide powder'],
          '{obj} is placed on a clean laboratory dish before temperature exposure begins. '
          'The intact material and whole dish are visible. No vapor plume, smoke, '
          'disappearance or transformation is depicted.')
    group(121, ['red and blue', 'yellow and red', 'green and blue', 'cyan and magenta', 'pink and white'],
          'A mixing bowl contains equal amounts of {obj} paint, as two separate adjacent '
          'pools with a clean unmixed boundary. A hand holds a clean stirring tool just '
          'above the bowl. Stirring and combining have not begun. No streaks or blended '
          'paint are present. Both colors and the complete bowl are clearly visible.')
    group(126, ['copper', 'magnesium', 'lithium', 'sodium', 'potassium'],
          'A small intact piece of {obj} rests in a simple laboratory holder on a '
          'fireproof surface. An inactive ignition tool is poised nearby. This is '
          'before ignition: the metal is unburned, with no flame, smoke or glow.')
    for i, (obj, surface) in enumerate(zip(
            ['an intact egg', 'an intact vase', 'an intact glass bottle', 'an intact glass cup', 'an intact porcelain plate'],
            ['a rugged rock surface', 'a concrete wall', 'a rugged rock surface',
             'a solid metal surface', 'a sturdy wooden table']), 131):
        rows[i] = (f'A hand holds {obj} at a visible distance from {surface}, ready to throw '
                   'the object toward it. Show the complete object, target and clear space '
                   'between them. No release, impact, breakage or debris has occurred.')
    for i, (source_liquid, target_liquid, vessel) in enumerate(zip(
            ['oil', 'honey', 'vinegar', 'juice', 'vinegar'], ['water', 'milk', 'water', 'water', 'oil'],
            ['a clear glass', 'a clear glass', 'a clear glass', 'a clear glass', 'an open bottle']), 136):
        rows[i] = (f'A hand holds {vessel} of {source_liquid} upright just above and beside '
                   f'a separate half-full glass of {target_liquid}. Both liquid-filled '
                   'containers are clearly visible before pouring begins. No fluid stream or mixing.')
    for i, (obj, pile) in enumerate(zip(
            ['a small burning paper ball', 'a small burning match', 'a small burning stick',
             'a small burning candle', 'a small burning piece of cloth'],
            ['dry paper', 'dry leaves', 'hay', 'dry grass', 'old newspapers']), 141):
        rows[i] = (f'{obj} is held just above and beside a pile of {pile}, before being '
                   'thrown into it. The small source is already burning, but the entire '
                   'target pile remains untouched and unlit. Show both with a clear gap.')
    for i, (liquid, target) in enumerate(zip(
            ['colorless vinegar', 'hydrochloric acid', 'colorless soda water', 'lemon juice', 'orange juice'],
            ['purple litmus solution', 'purple litmus solution', 'blue litmus solution',
             'red cabbage juice', 'baking soda']), 146):
        rows[i] = (f'A small transparent container of {liquid} is held upright beside '
                   f'a separate clear glass containing {target}. Both materials are '
                   'visible and unmixed, before any addition or reaction. No pouring stream yet.')
    group(151, ['a piece of bread', 'an apple slice', 'a sheet of paper', 'a cotton ball', 'a sponge'],
          'In a realistic laboratory close-up, a glass dropper containing clear concentrated '
          'sulfuric acid is held above {obj} on a tray. The target is intact, untreated '
          'and uncharred, and no liquid has been dispensed. Both objects are visible.')
    group(156, ['a clean smooth piece of iron in a humid setting', 'a clean smooth piece of steel in a humid setting',
                'a shiny new metal chair outdoors', 'a polished new metal railing outdoors',
                'a bright new metal bedframe in a damp room'],
          'A realistic photograph of {obj}, before prolonged weather exposure. '
          'The metal is intact, clean and uncorroded, with no rust, flaking or decay.')
    assert set(rows) == set(range(1, 161))
    return rows


def main():
    source = json.loads((RUN / 'paired_prompts.json').read_text())
    static = descriptions()
    archive = RUN / 'archives' / f'initial_prompt_v1_{time.time_ns()}'
    archive.mkdir(parents=True)
    for name in ('protocol.json', 'paired_prompts.json', 'smoke_inspection.png'):
        shutil.copy2(RUN / name, archive / name)
    for row in source:
        folder = RUN / 'candidates' / row['case_id']
        if folder.exists():
            shutil.move(str(folder), str(archive / row['case_id']))
        before = row['video_prompt']
        row['initial_state_description'] = static[row['source_index'] + 1]
        row['image_prompt'] = STYLE + row['initial_state_description']
        row['image_prompt_version'] = 'static_initial_v2'
        assert hashlib.sha256(before.encode()).hexdigest() == row['video_prompt_sha256']
        input_dir = RUN / 'inputs' / row['case_id']
        assert (input_dir / 'prompt.txt').read_text() == before
        (input_dir / 'image_prompt.txt').write_text(row['image_prompt'])
    (RUN / 'paired_prompts.json').write_text(json.dumps(source, indent=2, ensure_ascii=False) + '\n')
    protocol = json.loads((RUN / 'protocol.json').read_text())
    protocol.update(protocol='phygen_i2v_qwen21_v2', initial_prompt_version='static_initial_v2',
                    image_prompt_construction='Explicit pre-action scene description derived from original text; original video prompts unchanged.',
                    prior_candidates_archive=str(archive), status='revised initial-frame smoke; video experiments paused')
    (RUN / 'protocol.json').write_text(json.dumps(protocol, indent=2) + '\n')
    review = {'reviewed_at': time.time(), 'image_actually_read': str(archive / 'smoke_inspection.png'),
              'cases_reviewed': ['phygen_001', 'phygen_061', 'phygen_091', 'phygen_121'],
              'candidates_reviewed': 12, 'decision': 'Revise before full generation',
              'findings': {'phygen_001': 'All three pour into a cup rather than out; reject.',
                           'phygen_061': 'Reflective scene recognizable; some candidates have almost no approach space.',
                           'phygen_091': 'Ice or frost is already present; reject as pre-cooling initial states.',
                           'phygen_121': 'Mixing/stirring already started; reject as pre-action states.'}}
    (RUN / 'logs' / 'visual_review_v1.json').write_text(json.dumps(review, indent=2) + '\n')
    print('Updated 160 image prompts; video prompt hashes unchanged; archived all prior candidates:', archive)


if __name__ == '__main__':
    main()
