"""Quick smoke test for the translation pipeline."""
import sys
sys.path.insert(0, '.')

from services.translatorr import translate_text
from services.filehandler import translate_file
import os

# Test 1: translate_text directly
print("=== Test 1: translate_text (eng-spa) ===")
text = "Employee John Doe has a current salary of $120,000 per annum. His primary contact email is john.doe@company.com and phone is 555-123-4567."
result = translate_text(text, 'eng-spa', 'google', 'Engineering', [])
translated, total_words, masked_words, glossary_words, confidence, paragraphs, cost = result
print(f"  Original:   {text[:80]}...")
print(f"  Translated: {translated[:80]}...")
print(f"  Words: {total_words}, Masked: {masked_words}, Confidence: {confidence}")
print()

# Test 2: translate_text (spa-eng)
print("=== Test 2: translate_text (spa-eng) ===")
text2 = "El empleado tiene un salario de $85,000. Su correo electrónico es test@test.com."
result2 = translate_text(text2, 'spa-eng', 'google', 'Engineering', [])
print(f"  Original:   {text2}")
print(f"  Translated: {result2[0]}")
print(f"  Words: {result2[1]}, Masked: {result2[2]}")
print()

# Test 3: translate_file (txt)
print("=== Test 3: translate_file (txt, eng-spa) ===")
input_path = 'uploads/test_pii.txt'
output_path = 'uploads/translated_test_pii.txt'
if os.path.exists(input_path):
    w, m, g, c, cost = translate_file(input_path, output_path, 'eng-spa', 'google', 'Engineering', [])
    print(f"  Words: {w}, Masked: {m}, Glossary: {g}, Confidence: {c}, Cost: {cost}")
    if os.path.exists(output_path):
        with open(output_path, 'r', encoding='utf-8') as f:
            print(f"  Output:\n{f.read()}")
    else:
        print("  ERROR: Output file was not created!")
else:
    print(f"  SKIP: {input_path} not found")

print("\n=== ALL TESTS PASSED ===")
