"""Local creative controls and deterministic Omni prompt assembly; no API calls."""
import re
import math

TEXT_FIELDS = ('camera', 'sound', 'music', 'dialogue')
SPEECH_FIELDS = ('dialogueLanguage', 'dialogueSpeaker', 'dialogueVoice', 'exactDialogue')
IMAGE_KINDS = ('person', 'object', 'scene')
AUDIO_KINDS = ('voice', 'ambience', 'soundtrack')


def validate_creative(value, *, continuation=True, opening_reference=False):
    if not isinstance(value, dict) or set(value) - {*TEXT_FIELDS, *SPEECH_FIELDS, 'dialogueMode', 'images', 'audios', 'firstFrame', 'lastFrame'}:
        raise ValueError('Provide supported creative controls only.')
    result = {}
    for field in TEXT_FIELDS:
        text = value.get(field, '')
        if not isinstance(text, str) or len(text) > 1000:
            raise ValueError(f'Keep {field} direction under 1,000 characters.')
        if text.strip():
            result[field] = text.strip()
    mode = value.get('dialogueMode', 'auto')
    if mode not in ('auto', 'off', 'exact'):
        raise ValueError('Choose scripted dialogue, an exact line, or no speech.')
    if 'dialogueMode' in value:
        result['dialogueMode'] = mode
    for field in SPEECH_FIELDS:
        text = value.get(field, '')
        limit = 180 if field == 'exactDialogue' else 150
        if not isinstance(text, str) or len(text) > limit or any(ord(char) < 32 for char in text):
            raise ValueError(f'Keep {field} to one short line under {limit} characters.')
        if text.strip():
            result[field] = text.strip()
    if mode == 'exact' and not result.get('exactDialogue'):
        raise ValueError('Write the exact spoken line, or choose scripted dialogue.')
    if result.get('dialogueLanguage') and not re.fullmatch(r'[A-Za-z][A-Za-z -]{0,79}', result['dialogueLanguage']):
        raise ValueError('Write a language name such as English or Mandarin Chinese.')
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


def speech_word_limit(seconds):
    return min(12, max(1, math.floor((seconds - 1.5) * 2)))


def validate_speech(speech, seconds, creative):
    mode = creative.get('dialogueMode', 'auto')
    soundtrack = any(item['kind'] == 'soundtrack' for item in creative.get('audios', []))
    if speech is None:
        if mode == 'exact' and not soundtrack:
            raise ValueError('The exact dialogue line is required.')
        return None
    if mode == 'off' or soundtrack:
        raise ValueError('This scene must not contain speech.')
    fields = {'speaker', 'voice', 'language', 'text', 'picture', 'audio'}
    if not isinstance(speech, dict) or set(speech) != fields:
        raise ValueError('Speech needs one speaker, voice, language, exact text, picture and audio binding.')
    for field in ('speaker', 'voice', 'language', 'text'):
        text = speech[field]
        if (not isinstance(text, str) or not text.strip() or len(text) > (180 if field == 'text' else 150)
                or any(ord(char) < 32 for char in text) or '<' in text or '>' in text):
            raise ValueError(f'Use a short plain-text {field}, without prompt tags or newlines.')
    if not re.fullmatch(r'[A-Za-z][A-Za-z -]{0,79}', speech['language']):
        raise ValueError('Use a language name, not a language tag.')
    units = re.findall(r'[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]|[^\W_]+(?:[\x27’-][^\W_]+)*', speech['text'], re.UNICODE)
    if not units or len(units) > speech_word_limit(seconds):
        raise ValueError(f'Use at most {speech_word_limit(seconds)} spoken words/characters in this {seconds:g}-second scene.')
    if not speech['text'].rstrip().endswith(('.', '!', '?', '。', '！', '？', '…')):
        raise ValueError('Finish the spoken line with sentence punctuation.')
    for field, collection in (('picture', 'images'), ('audio', 'audios')):
        index = speech[field]
        if index is not None:
            items = creative.get(collection, [])
            if type(index) is not int or not 1 <= index <= len(items):
                raise ValueError(f'The speech {field} must name an uploaded reference or be null.')
            if field == 'audio' and items[index - 1]['kind'] != 'voice':
                raise ValueError('Bind spoken dialogue only to a voice reference.')
    for setting, field in (('dialogueSpeaker', 'speaker'), ('dialogueVoice', 'voice'), ('dialogueLanguage', 'language')):
        if creative.get(setting) and speech[field] != creative[setting]:
            raise ValueError(f'Preserve the configured {field} exactly.')
    if mode == 'exact' and speech['text'] != creative.get('exactDialogue'):
        raise ValueError('Preserve every word and punctuation mark of the exact dialogue line.')
    return dict(speech)


def assemble_prompt(beat, style, creative, *, opening_reference=False, speech=None, camera=None, sound=None, music=None):
    """References retain upload order; Grok never gets to rewrite their roles."""
    definitions = []
    for index, image in enumerate(creative.get('images', []), 1):
        definitions.append(f'<Subject {index}> is {image["description"]} in <Picture {index}>.')
    if opening_reference:
        definitions.append('<Picture 1> guides the appearance of the opening scene.')
    for index, audio in enumerate(creative.get('audios', []), 1):
        role = {'voice': 'voice-timbre reference', 'ambience': 'ambient sound reference',
                'soundtrack': "target video's complete audio track, reused unchanged"}[audio['kind']]
        if speech and speech.get('audio') == index:
            speaker = f'<Subject {speech["picture"]}>' if speech.get('picture') else speech['speaker']
            definitions.append(f'<Audio {index}> is the voice-timbre reference for {speaker} (S1): {audio["description"]}.')
        else:
            definitions.append(f'<Audio {index}> is the {role}: {audio["description"]}.')
    lines = ['subject_definitions:\n' + '\n'.join(definitions)] if definitions else []
    soundtrack = any(item['kind'] == 'soundtrack' for item in creative.get('audios', []))
    def sentence(text):
        text = text.strip()
        return text if text.endswith(('.', '!', '?', '。', '！', '？', '…')) else text + '.'
    body = ' '.join(sentence(part) for part in (style, beat, creative.get('camera') or camera or 'The camera holds a steady shot.') if part.strip())
    if speech and not soundtrack:
        speaker = f'<Subject {speech["picture"]}>, {speech["speaker"]}' if speech.get('picture') else speech['speaker']
        voice_ref = f', using the voice timbre from <Audio {speech["audio"]}>' if speech.get('audio') else ''
        body += f' {speaker}, with {speech["voice"]}{voice_ref} (S1), says: <d>[{speech["language"]}] {speech["text"]}</d> Their lips close after the line.'
    elif not soundtrack:
        body += ' No dialogue, singing or intelligible background voices; all visible characters keep their lips closed.'
    if definitions:
        lines.append('summary: A single continuous reference-guided shot.')
        retention = [f'<Subject {i}>: fully_preserved - retain the defined identity and appearance.' for i in range(1, len(creative.get('images', [])) + 1)]
        retention += [f'<Audio {i}>: {"reused" if a["kind"] == "soundtrack" else "reference"} - retain its defined role.' for i,a in enumerate(creative.get('audios', []), 1)]
        lines.append('retention_analysis: ' + (' '.join(retention) or 'Preserve the opening appearance reference.'))
        lines.append('detailed_description: [Shot 1] ' + body)
    else:
        lines.append('integrated_multimodal_description: [Shot 1] ' + body)
    lines.append('overall_soundscape: ' + ('Follow the complete referenced soundtrack.' if soundtrack else creative.get('sound') or sound or 'Natural ambience and synchronized physical sounds.'))
    lines.append('non_diegetic_music: ' + ('N/A' if soundtrack else creative.get('music') or music or 'N/A'))
    return '\n\n'.join(lines)
