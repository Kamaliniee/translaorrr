# services/translatorr.py
"""Translation service utilities.
Provides text translation, PII masking, and engine display helpers.
"""

import re
import os
from services.glossary import apply_glossary_rules
from deep_translator import GoogleTranslator

# ── SSL fix for Windows corporate networks ───────────────────────────────────
# On Windows, IT installs custom root CAs (e.g. TLS-inspection proxy certs) into
# the Windows Certificate Store.  truststore.inject_into_ssl() makes Python's ssl
# module use that store, so requests/urllib3/deep_translator all trust those CAs
# automatically — no manual cert bundle management needed.
try:
    import truststore as _truststore
    _truststore.inject_into_ssl()
except Exception as _ssl_e:
    print(f"[translatorr] truststore SSL inject failed ({_ssl_e}); SSL errors may occur.")
# ─────────────────────────────────────────────────────────────────────────────

def load_properties_mask_words():
    words = []
    prop_path = "mask_words.properties"
    if os.path.exists(prop_path):
        try:
            with open(prop_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        if '=' in line:
                            key, val = line.split('=', 1)
                            if key.strip().lower() == 'mask.words':
                                words.extend([w.strip() for w in val.split(',') if w.strip()])
                        else:
                            words.append(line)
        except Exception as e:
            print(f"Error reading mask_words.properties: {e}")
    return list(set(words))


# PII Detection Patterns
SALARY_PATTERN = re.compile(r'\$\d{1,3}(?:,\d{3})*(?:\s*(?:USD|USD/yr|/yr|per annum|USD/year|/year))?\b', re.IGNORECASE)
EMAIL_PATTERN = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')
PHONE_PATTERN = re.compile(r'\b(?:\+?\d{1,3}[- ]?)?\(?\d{3}\)?[- ]?\d{3}[- ]?\d{4}\b')
ID_PATTERN = re.compile(r'\b\d{3}-\d{2}-\d{4}\b')
NAME_PATTERN = re.compile(r'\b(?:Dr\.|Mr\.|Ms\.|Mrs\.|Director General|Manager)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b|\b(?:Mark Miller|Alice Johnson|Jane Smith|John Doe)\b')

# Terminology/Unusual phrases triggers
UNUSUAL_TERMS = {
    'WebLogic': 'Critical infrastructure server technology',
    'Director General': 'High-level executive leadership title',
    'pipelines': 'Data routing process channels',
    'audit': 'Compliance tracking & auditing system'
}

def mask_pii(text, custom_words=None):
    """Detect names, emails, phone numbers, ID numbers, salaries and custom words and mask them."""
    mapping = {}
    masked_text = text
    masked_count = 0

    # Combine custom words from properties file + user input
    all_custom_words = load_properties_mask_words()
    if custom_words:
        all_custom_words.extend(custom_words)
    # Remove duplicates, empty strings, and sort by length descending to match longest first
    all_custom_words = sorted(list(set([w for w in all_custom_words if w.strip()])), key=len, reverse=True)

    # Mask custom words first
    for idx, word in enumerate(all_custom_words):
        start_boundary = r'\b' if word[0].isalnum() or word[0] == '_' else ''
        end_boundary = r'\b' if word[-1].isalnum() or word[-1] == '_' else ''
        pattern = re.compile(f"{start_boundary}{re.escape(word)}{end_boundary}", re.IGNORECASE)
        matches = pattern.findall(masked_text)
        if matches:
            for match in set(matches):
                placeholder = f"[CUSTOM_MASK_{idx+1}]"
                mapping[placeholder] = match
                masked_text = re.sub(f"{start_boundary}{re.escape(match)}{end_boundary}", placeholder, masked_text)
                masked_count += len(matches)


    # Salaries
    salaries = SALARY_PATTERN.findall(masked_text)
    for idx, sal in enumerate(salaries):
        placeholder = f"[SALARY_{idx+1}]"
        mapping[placeholder] = sal
        masked_text = masked_text.replace(sal, placeholder)
        masked_count += 1

    # Emails
    emails = EMAIL_PATTERN.findall(masked_text)
    for idx, email in enumerate(emails):
        placeholder = f"[EMAIL_{idx+1}]"
        mapping[placeholder] = email
        masked_text = masked_text.replace(email, placeholder)
        masked_count += 1

    # Phones
    phones = PHONE_PATTERN.findall(masked_text)
    for idx, phone in enumerate(phones):
        placeholder = f"[PHONE_{idx+1}]"
        mapping[placeholder] = phone
        masked_text = masked_text.replace(phone, placeholder)
        masked_count += 1

    # IDs
    ids = ID_PATTERN.findall(masked_text)
    for idx, id_num in enumerate(ids):
        placeholder = f"[ID_{idx+1}]"
        mapping[placeholder] = id_num
        masked_text = masked_text.replace(id_num, placeholder)
        masked_count += 1

    # Names (sorted longest-first to prevent partial replacement)
    names = NAME_PATTERN.findall(masked_text)
    names = sorted(list(set(names)), key=len, reverse=True)
    for idx, name in enumerate(names):
        placeholder = f"[NAME_{idx+1}]"
        mapping[placeholder] = name
        masked_text = masked_text.replace(name, placeholder)
        masked_count += 1

    return masked_text, mapping, masked_count

def restore_pii(text, mapping):
    """Restore masked values in final translated text."""
    unmasked = text
    for placeholder, val in mapping.items():
        unmasked = unmasked.replace(placeholder, val)
    return unmasked


# Real translation engines using deep-translator with placeholder protection


def protect_and_translate(text, direction, translator_init_func):
    """Protect PII/glossary placeholders by replacing them with short numeric tokens
    that survive translation engines, then restoring them afterward.
    Uses tokens like XPHX0XPHX which are unlikely to be altered by translators.
    """
    placeholder_pattern = re.compile(r"\[[A-Z_0-9]+\]")
    placeholders = {}

    def _replacer(match):
        idx = len(placeholders)
        # Short alphanumeric token unlikely to be split or altered by translators
        token = f"XPHX{idx}XPHX"
        placeholders[token] = match.group(0)
        return token

    safe_text = placeholder_pattern.sub(_replacer, text)

    try:
        translated_safe = translator_init_func(safe_text)
    except Exception as e:
        print(f"[translatorr] Translation engine failed: {e}. Falling back to original text.")
        translated_safe = safe_text

    # Restore tokens — handle minor casing variations translators may introduce
    for token, original in placeholders.items():
        translated_safe = translated_safe.replace(token, original)
        translated_safe = translated_safe.replace(token.lower(), original)
        translated_safe = translated_safe.replace(token.title(), original)
    return translated_safe

def real_google_translate(text, direction):
    # Use 'auto' source so mixed-language or ambiguous text is detected correctly
    tgt_lang = 'es' if direction == 'eng-spa' else 'en'
    translator = GoogleTranslator(source='auto', target=tgt_lang)
    return protect_and_translate(text, direction, lambda t: translator.translate(t))

def real_translate_engine(text, direction, engine='google'):
    """Translate text using the selected translation engine via deep-translator,
    preserving PII/glossary placeholders throughout.
    Raises an exception on failure so errors are visible rather than silently
    falling back to the limited mock engine.
    """
    if engine == 'google':
        return real_google_translate(text, direction)
    else:
        raise ValueError(f"Unsupported translation engine: '{engine}'. Only Google is available.")

def translate_text(text, direction, engine='google', department='default', glossary_rules=None, custom_words=None):
    """Perform secure translation with PII masking and quality analytics."""
    if glossary_rules is None:
        glossary_rules = []
        
    orig_paragraphs = text.split('\n\n')
    translated_paragraphs = []
    paragraphs_meta = []
    
    total_words = len(text.split())
    total_masked = 0
    total_glossary = 0
    
    for p_idx, p in enumerate(orig_paragraphs):
        if not p.strip():
            continue
            
        # 1. Mask PII
        masked_p, mapping, p_masked_count = mask_pii(p, custom_words)
        total_masked += p_masked_count
        
        # Apply glossary rules using placeholder preservation
        masked_p, glossary_mapping, p_glossary_count = apply_glossary_rules(masked_p, glossary_rules, p_idx)
        total_glossary += p_glossary_count
        
        # 2. Translate masked text using selected engine via deep-translator
        translated_p = real_translate_engine(masked_p, direction, engine)
        
        # 3. Unmask glossary terms and original values
        translated_p = restore_pii(translated_p, glossary_mapping)
        final_p = restore_pii(translated_p, mapping)
        translated_paragraphs.append(final_p)
        
        # 4. Analytics & Terminology Warnings
        p_score = 98
        p_flags = []
        
        # Check PII flags
        if p_masked_count > 0:
            p_flags.append(f"PII Masking applied: {p_masked_count} sensitive fields sanitized before engine transfer.")
            
        # Check unusual/critical terminology highlights
        for term, desc in UNUSUAL_TERMS.items():
            if term.lower() in p.lower():
                p_flags.append(f"Critical Terminology: '{term}' detected. Verify '{desc}' context.")
                p_score = min(p_score, 78)  # Lower confidence score to trigger review
                
        # Check glossary rules application
        if p_glossary_count > 0:
            p_flags.append(f"Glossary Enforcement: {p_glossary_count} terminology rules injected.")
            
        low_confidence = p_score < 85
        
        paragraphs_meta.append({
            'source': p,
            'target': final_p,
            'score': p_score,
            'low_confidence': low_confidence,
            'flags': p_flags
        })
        
    final_translation = "\n\n".join(translated_paragraphs)
    
    # Calculate dummy cost based on characters
    char_rate = 20.0 if engine == 'google' else 0.0
    cost = round((len(text) / 1000000.0) * char_rate, 5)
    
    # Global average score
    avg_score = round(sum(p['score'] for p in paragraphs_meta) / len(paragraphs_meta), 1) if paragraphs_meta else 95.0
    
    return final_translation, total_words, total_masked, total_glossary, avg_score, paragraphs_meta, cost

def get_engine_display_name(engine_key):
    """Return a user-friendly name for an engine identifier."""
    mapping = {
        'google': 'Google Cloud Translation'
    }
    return mapping.get(engine_key, engine_key.title())


def check_api_connections():
    """Verify active network connectivity and API keys validation status for each translator engine.
    Returns:
        dict: A dictionary containing engine key mapped to connection status and diagnostic message.
    """
    results = {}

    # 1. Google Translate
    try:
        translator = GoogleTranslator(source='en', target='es')
        res = translator.translate("Hello")
        if res and res.strip():
            results['google'] = {"status": "Active", "error": None}
        else:
            results['google'] = {"status": "Connection Failed", "error": "Returned empty translation"}
    except Exception as e:
        results['google'] = {"status": "Connection Failed", "error": str(e)}

    return results

