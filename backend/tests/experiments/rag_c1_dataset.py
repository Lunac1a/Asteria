"""Frozen synthetic D1 input; no application imports or external services."""
import hashlib
import random

SEED = 20261023


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def verify_index_identity(rows, backend, model):
    """Database IDs include the backend namespace; a model name alone is insufficient."""
    expected = (f'{backend}:{model}', 'ready', 10)
    if list(map(tuple, rows)) != [expected]:
        raise AssertionError('Indexed model/state/chunk-count does not match the D1 contract')


def dataset():
    documents, queries = [], []
    names = ['Amber', 'Birch', 'Cobalt', 'Dahlia', 'Elm', 'Flint', 'Garnet',
             'Hazel', 'Indigo', 'Jade', 'Kestrel', 'Lilac', 'Maple', 'Nectar',
             'Opal', 'Pine', 'Quartz', 'Reed', 'Saffron', 'Topaz']
    for i, name in enumerate(names):
        facility = f'{name} facility Z{i:02d}'
        facts = [f'{facility} maintenance interval is {11+i} days.',
                 f'{facility} storage capacity is {101+i*7} litres.',
                 f'{facility} operating constraint requires valve code SAFE{i:02d}.']
        paragraph = ' '.join(facts) + f' This synthetic manual describes only {facility}. '
        content = (paragraph * (8000 // len(paragraph) + 1))[:7999] + '.'
        chunks = [content[start:start+1000] for start in range(0, 8000, 800)]
        documents.append(dict(name=f'{name}-Z{i:02d}.txt', content=content,
                              sha256=digest(content), chunk_hashes=list(map(digest, chunks))))
        for j, topic in enumerate(['maintenance interval', 'storage capacity', 'operating constraint']):
            queries.append(dict(id=f'q{i:02d}-{j}', document=documents[-1]['name'],
                                question=f'According to the manual, what is the {topic} of {facility}?',
                                fact=facts[j], answer=facts[j],
                                allowed_ordinals=[n for n, c in enumerate(chunks) if facts[j] in c]))
    random.Random(SEED).shuffle(queries)
    return dict(version='D1-v1', seed=SEED, documents=documents, queries=queries)
