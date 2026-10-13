"""Local creative controls and deterministic Omni prompt assembly; no API calls."""
import re

TEXT_FIELDS = ('camera', 'sound', 'music', 'dialogue')
IMAGE_KINDS = ('person', 'object', 'scene')
AUDIO_KINDS = ('voice', 'ambience', 'soundtrack')


def validate_creative(value, *, continuation=True, opening_reference=False):
    if not isinstance(value, dict) or set(value) - {*TEXT_FIELDS, 'images', 'audios', 'firstFrame', 'lastFrame'}:
        raise ValueError('Provide supported creative controls only.')
    result = {}
    for field in TEXT_FIELDS:
        text = value.get(field, '')
        if not isinstance(text, str) or len(text) > 1000:
            raise ValueError(f'Keep {field} direction under 1,000 characters.')
        if text.strip():
            result[field] = text.strip()
    for field, kinds, limit in (('images', IMAGE_KINDS, 9), ('audios', AUDIO_KINDS, 2 if continuation else 3)):
        items = value.get(field, [])
        if not isinstance(items, list) or len(items) > limit:
            raise ValueError(f'Use at most {limit} {field} with these continuation settings.')
        validated = []
        for item in items:
            if not isinstance(item, dict) or set(item) != {'id', 'kind', 'description'}:
                raise ValueError('Each reference needs an ID, role and description.')
            if (not isinstance(item['id'], str) or not re.fullmatch(r'[a-f0-9]{24}', item['id'])
                    or item['kind'] not in kinds or not isinstance(item['description'], str)
                    or not item['description'].strip() or len(item['description']) > 300):
                raise ValueError('Give every reference a valid role and a description of 1–300 characters.')
            validated.append(dict(item, description=item['description'].strip()))
        if len({item['id'] for item in validated}) != len(validated):
            raise ValueError('A reference cannot appear twice in the same list.')
        if validated:
            result[field] = validated
    # An opening scene has no generated continuation yet: require its own image.
    has_image = bool(result.get('images'))
    if result.get('audios') and not has_image:
        raise ValueError('Audio references need an image reference for the opening scene.')
    if sum(item['kind'] == 'soundtrack' for item in result.get('audios', [])) > 1:
        raise ValueError('Choose only one complete soundtrack.')
    for field in ('firstFrame', 'lastFrame'):
        asset_id = value.get(field, '')
        if not isinstance(asset_id, str) or (asset_id and not re.fullmatch(r'[a-f0-9]{24}', asset_id)):
            raise ValueError('Choose an uploaded image for a keyframe.')
        if asset_id:
            if not has_image:
                raise ValueError('Keyframes need at least one image reference.')
            result[field] = asset_id
    if opening_reference and result.get('images'):
        raise ValueError('Use the numbered image references or the legacy opening reference, not both.')
    return result


def assemble_prompt(beat, style, creative, *, opening_reference=False):
    """References retain upload order; Grok never gets to rewrite their roles."""
    definitions = []
    for index, image in enumerate(creative.get('images', []), 1):
        definitions.append(f'<Picture {index}> is the {image["kind"]} reference: {image["description"]}.')
    if opening_reference:
        definitions.append('<Picture 1> guides the appearance of the opening scene.')
    for index, audio in enumerate(creative.get('audios', []), 1):
        role = {'voice': 'voice-timbre reference', 'ambience': 'ambient sound reference',
                'soundtrack': "target video's complete audio track, reused unchanged"}[audio['kind']]
        definitions.append(f'<Audio {index}> is the {role}: {audio["description"]}.')
    lines = ['subject_definitions:\n' + '\n'.join(definitions)] if definitions else []
    lines.append('Scene: ' + beat.strip())
    if style.strip():
        lines.append('Look: ' + style.strip())
    lines.append('Camera: ' + creative.get('camera', 'One coherent camera move suited to the action.'))
    soundtrack = any(item['kind'] == 'soundtrack' for item in creative.get('audios', []))
    if not soundtrack:
        lines.append('Sound: ' + creative.get('sound', 'Natural ambience and sounds synchronized to the visible action.'))
        if creative.get('music'):
            lines.append('Music: ' + creative['music'])
        if creative.get('dialogue'):
            lines.append('Dialogue: ' + creative['dialogue'])
    return '\n\n'.join(lines)
