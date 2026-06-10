# services/glossary.py
"""Glossary service utilities.
Provides database rule retrieval and quality terminology preservation/translation masking.
"""

import re

def get_glossary_rules(conn, direction):
    """Return glossary rules from DB for the given direction or 'all'."""
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT source_term, target_term FROM glossary WHERE direction = 'all' OR direction = %s", (direction,))
        return [{'source_term': row['source_term'], 'target_term': row['target_term']} for row in cursor.fetchall()]
    except Exception as e:
        print(f"Error fetching glossary rules: {e}")
        return []

def apply_glossary_rules(text, glossary_rules, p_idx):
    """Mask glossary terms with placeholders to preserve or translate them consistently.
    Returns: masked_text, glossary_mapping, p_glossary_count.
    """
    p_glossary_count = 0
    glossary_mapping = {}
    masked_p = text
    
    # Sort glossary rules by length of source_term descending to match longer terms first
    sorted_rules = sorted(glossary_rules, key=lambda r: len(r.get('source_term', '') or ''), reverse=True)
    for g_idx, rule in enumerate(sorted_rules):
        src = rule.get('source_term', '')
        tgt = rule.get('target_term', '')
        if not src:
            continue
        
        # Smart boundary matching: only use \b if the starting/ending character is alphanumeric or underscore
        start_boundary = r'\b' if src[0].isalnum() or src[0] == '_' else ''
        end_boundary = r'\b' if src[-1].isalnum() or src[-1] == '_' else ''
        pattern = re.compile(f"{start_boundary}{re.escape(src)}{end_boundary}", re.IGNORECASE)
        
        matches = pattern.findall(masked_p)
        if matches:
            for match in set(matches):
                placeholder = f"[GLOSSARY_{p_idx}_{g_idx}]"
                replacement = tgt if tgt else match
                glossary_mapping[placeholder] = replacement
                masked_p = re.sub(f"{start_boundary}{re.escape(match)}{end_boundary}", placeholder, masked_p)
                p_glossary_count += len(matches)
                
    return masked_p, glossary_mapping, p_glossary_count
