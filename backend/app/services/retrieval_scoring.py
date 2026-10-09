"""Pure retrieval ranking. No model calls, query logs, or answer generation.

Dense scores remain primary. The optional conservative lexical rescue only
admits a near-threshold candidate when >=3 informative query terms and >=50%
of its terms appear verbatim in the source. A filename/page alone is not evidence.
"""
import math
import re
import unicodedata

# Generic request/function words, not topic-specific answer vocabulary.
STOP_WORDS = frozenset("""a an the and or to of in on at by for from with as is are
was were be been being do does did it its this that these those i we you your my
what which who when where why how if then than can could would should will may
please according based page pages pdf document documents file text source cite
answer brief briefly keep explain describe tell give show happens happen about
under all any some into so same also more most only not no""".split())


def lexical_terms(text, document_names=()):
    text = unicodedata.normalize("NFKC", text).casefold()
    # Metadata must not create a lexical match by itself. Only remove actual names.
    for name in sorted(document_names, key=len, reverse=True):
        text = text.replace(unicodedata.normalize("NFKC", name).casefold(), " ")
    text = re.sub(r"\b(?:pages?|p\.)\s*\d+(?:\s*[-–]\s*\d+)?\b|第\s*\d+\s*页", " ", text)
    # Preserve short mathematical identifiers such as h(n); bare single letters
    # and page numbers otherwise carry too little retrieval information.
    formulas = re.findall(r"\b[a-z]+\([a-z]+\)", text)
    text = re.sub(r"\b[a-z]+\([a-z]+\)", " ", text)
    terms = set(formulas)
    terms.update(t for t in re.findall(r"[a-z][a-z0-9]+",text) if t not in STOP_WORDS)
    for run in re.findall(r"[\u3400-\u9fff]+",text):
        terms.update(run[i:i+2] for i in range(len(run)-1))
    return terms


def rank_candidates(vector, candidates, question, document_names, threshold, *, lexical_rescue=False):
    """Candidates have id/content/vector. Return scores and privacy-safe reasons.

    This does not confer answer support: downstream citation/evidence validation
    still determines whether returned text actually answers the question.
    """
    query_terms = lexical_terms(question, document_names) if lexical_rescue else set()
    ranked=[]
    for candidate in candidates:
        passage=candidate['vector']
        if len(vector)!=len(passage): continue
        norm=math.sqrt(sum(x*x for x in vector)*sum(x*x for x in passage)) or 1
        score=sum(x*y for x,y in zip(vector,passage))/norm
        if not math.isfinite(score): continue
        overlap=set()
        if lexical_rescue and score < threshold and score >= max(.30, threshold-.05):
            overlap=query_terms & lexical_terms(candidate['content'], document_names)
        coverage=len(overlap)/max(1,len(query_terms))
        rescue=len(overlap)>=3 and coverage>=.5
        ranked.append({'id':candidate['id'],'score':score,'accepted':score>=threshold or rescue,
                       'reason':'dense' if score>=threshold else 'lexical_rescue' if rescue else 'below_threshold',
                       'overlap_count':len(overlap),'query_term_count':len(query_terms),'coverage':coverage})
    return sorted(ranked,key=lambda r:(-r['score'],r['id']))
