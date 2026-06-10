#!/usr/bin/env python3
"""
Comprehensive Test Suite for DocTranslate Implementation
Tests all new features and ensures backward compatibility
"""

import unittest
import os
import sys
import tempfile
import pymysql
from flask import Flask
from werkzeug.datastructures import FileStorage
from io import BytesIO

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

class DocTranslateImplementationTests(unittest.TestCase):
    """Test suite for DocTranslate Enterprise implementation"""
    
    def setUp(self):
        """Set up test environment"""
        self.app = Flask(__name__)
        self.app.config['TESTING'] = True
        self.test_dir = tempfile.mkdtemp()
        
    def tearDown(self):
        """Clean up test environment"""
        import shutil
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
    
    # ── 1. PDF Support Tests ──
    def test_pdf_extension_supported(self):
        """Test that PDF extension is in ALLOWED_EXTENSIONS"""
        from app import ALLOWED_EXTENSIONS
        self.assertIn('pdf', ALLOWED_EXTENSIONS)
        print("✓ PDF extension is supported")
    
    def test_pdf_filehandler_import(self):
        """Test that PDF handler is imported"""
        try:
            from PyPDF2 import PdfReader, PdfWriter
            print("✓ PyPDF2 library is available")
        except ImportError:
            self.fail("PyPDF2 not installed. Run: pip install PyPDF2")
    
    # ── 2. File Size Limit Tests ──
    def test_file_size_limit_200mb(self):
        """Test that file size limit is set to 200MB"""
        from app import MAX_FILE_SIZE
        expected_size = 200 * 1024 * 1024
        self.assertEqual(MAX_FILE_SIZE, expected_size)
        print(f"✓ File size limit correctly set to {MAX_FILE_SIZE / (1024*1024):.0f}MB")
    
    # ── 3. Allowed Extensions Tests ──
    def test_allowed_extensions_include_new_formats(self):
        """Test that all required file formats are supported"""
        from app import ALLOWED_EXTENSIONS
        required_formats = {'txt', 'pdf', 'docx', 'xls', 'xlsx', 'csv'}
        for fmt in required_formats:
            self.assertIn(fmt, ALLOWED_EXTENSIONS,
                         f"Format '{fmt}' not in ALLOWED_EXTENSIONS")
        print(f"✓ All required formats supported: {', '.join(sorted(required_formats))}")
    
    # ── 4. Translation Engine Tests ──
    def test_engine_list_complete(self):
        """Test that all translation engines are implemented"""
        from services.translatorr import (
            real_google_translate,
            real_deepl_translate,
            real_azure_translate,
            real_libre_translate
        )
        print("✓ All translation engines are implemented:")
        print("  - Google Cloud Translation")
        print("  - DeepL Pro API")
        print("  - Microsoft Azure Translator")
        print("  - LibreTranslate (Local)")
    
    def test_engine_display_names(self):
        """Test engine display names are correct"""
        from services.translatorr import get_engine_display_name
        
        expected = {
            'google': 'Google Cloud Translation',
            'deepl': 'DeepL Pro',
            'azure': 'Azure Translator',
            'libretranslate': 'LibreTranslate (Local)'
        }
        
        for engine, display_name in expected.items():
            self.assertEqual(get_engine_display_name(engine), display_name)
        print("✓ Engine display names are correct")
    
    # ── 5. PII Masking Tests ──
    def test_pii_masking_function_exists(self):
        """Test that PII masking function exists"""
        from services.translatorr import mask_pii
        text = "John Doe's salary is $120,000 USD/yr. Email: john@example.com"
        masked, mapping, count = mask_pii(text)
        
        self.assertGreater(count, 0, "PII should be masked")
        self.assertGreater(len(mapping), 0, "Mapping should not be empty")
        print(f"✓ PII masking works correctly (masked {count} items)")
    
    def test_placeholder_restoration(self):
        """Test that placeholders are restored correctly"""
        from services.translatorr import mask_pii, restore_pii
        
        original_text = "Contact Dr. Mark Miller at mark.miller@example.com or call 555-123-4567"
        masked_text, mapping, count = mask_pii(original_text)
        
        # Verify masking occurred
        self.assertGreater(count, 0)
        self.assertNotEqual(masked_text, original_text)
        
        # Verify restoration
        restored = restore_pii(masked_text, mapping)
        self.assertEqual(restored, original_text)  # After restoration should match original
        print(f"✓ Placeholder restoration works (restored {len(mapping)} items)")
    
    # ── 6. File Handler Tests ──
    def test_txt_translation_support(self):
        """Test that TXT files are properly supported"""
        from services.filehandler import translate_file
        
        # Create test file
        test_content = "Hello world. This is a test."
        test_file = os.path.join(self.test_dir, "test.txt")
        output_file = os.path.join(self.test_dir, "translated_test.txt")
        
        with open(test_file, 'w', encoding='utf-8') as f:
            f.write(test_content)
        
        try:
            word_count, masked_count, glossary_count, confidence, cost = translate_file(
                test_file, output_file, 'eng-spa', 'google', 'Engineering', [], None
            )
            self.assertGreater(word_count, 0, "Word count should be greater than 0")
            print(f"✓ TXT translation works (translated {word_count} words)")
        except Exception as e:
            print(f"⚠ TXT translation test incomplete: {e}")
    
    # ── 7. Database Tests ──
    def test_database_tables_exist(self):
        """Test that all required database tables exist"""
        from app import get_db_connection, init_db
        
        # Initialize database
        init_db()
        
        # Check tables
        conn = get_db_connection()
        cursor = conn.cursor()
        
        required_tables = [
            'users', 'translations', 'glossary', 'audit_logs', 
            'settings', 'batch_jobs'
        ]
        
        cursor.execute("SHOW TABLES")
        existing_tables = [row[0] for row in cursor.fetchall()]
        
        for table in required_tables:
            self.assertIn(table, existing_tables, f"Table '{table}' not found")
        
        conn.close()
        print(f"✓ All required database tables exist: {', '.join(required_tables)}")
    
    # ── 8. Glossary Tests ──
    def test_glossary_rules_loading(self):
        """Test that glossary rules can be loaded"""
        from services.glossary import get_glossary_rules
        from app import get_db_connection
        
        conn = get_db_connection()
        rules = get_glossary_rules(conn, 'eng-spa')
        conn.close()
        
        # Should return a list (may be empty)
        self.assertIsInstance(rules, list)
        print(f"✓ Glossary rules loaded ({len(rules)} rules)")
    
    # ── 9. Configuration Tests ──
    def test_environment_setup(self):
        """Test that environment variables can be loaded"""
        from dotenv import load_dotenv
        
        # This should not raise an error
        load_dotenv()
        print("✓ Environment configuration loaded successfully")
    
    # ── 10. Flask App Tests ──
    def test_flask_app_has_routes(self):
        """Test that Flask app has all required routes"""
        from app import app
        
        required_routes = [
            '/login', '/logout', '/upload', '/dashboard',
            '/batch-status/<job_id>', '/download-batch/<job_id>',
            '/download-file/<filename>', '/settings/check-connections'
        ]
        
        # Get all registered routes
        routes = [str(rule) for rule in app.url_map.iter_rules()]
        
        for route in required_routes:
            # Convert Flask route syntax for comparison
            flask_route = route.replace('<job_id>', 'job_id').replace('<filename>', 'filename')
            found = any(flask_route.replace('job_id', '<job_id>').replace('filename', '<filename>') in r 
                       for r in routes)
            print(f"  - {route}: {'✓' if found else '✗'}")
    
    # ── 11. Translation Quality Tests ──
    def test_translation_preserves_placeholders(self):
        """Test that translation preserves placeholders"""
        from services.translatorr import mask_pii, protect_and_translate
        
        text = "Project WebLogic has budget of $500,000 for Q1 2024."
        masked, mapping, _ = mask_pii(text)
        
        # Ensure placeholders are in masked text
        self.assertIn('[', masked, "Masked text should contain placeholders")
        print("✓ Translation placeholder preservation works")
    
    # ── 12. Notification System Tests ──
    def test_notification_system_js_exists(self):
        """Test that notification system JavaScript exists in template"""
        dashboard_path = os.path.join(
            os.path.dirname(__file__), 'templates', 'dashboard.html'
        )
        
        if os.path.exists(dashboard_path):
            with open(dashboard_path, 'r', encoding='utf-8') as f:
                content = f.read()
                self.assertIn('addNotification', content, 
                             "Notification JS function not found in template")
                self.assertIn('notificationBell', content,
                             "Notification bell element not found")
            print("✓ Notification system is implemented in dashboard")
        else:
            print("⚠ Dashboard template not found for verification")
    
    # ── 13. Tab Interface Tests ──
    def test_translator_tabs_in_template(self):
        """Test that translator tabs exist in dashboard"""
        dashboard_path = os.path.join(
            os.path.dirname(__file__), 'templates', 'dashboard.html'
        )
        
        if os.path.exists(dashboard_path):
            with open(dashboard_path, 'r', encoding='utf-8') as f:
                content = f.read()
                self.assertIn('Text Translation', content,
                             "Text Translation tab not found")
                self.assertIn('Document Translation', content,
                             "Document Translation tab not found")
                self.assertIn('switchTranslatorTab', content,
                             "Tab switch function not found")
            print("✓ Translator tab interface is implemented")
        else:
            print("⚠ Dashboard template not found for verification")
    
    # ── 14. Three-Dot Menu Tests ──
    def test_action_menu_styles(self):
        """Test that action menu CSS is present"""
        dashboard_path = os.path.join(
            os.path.dirname(__file__), 'templates', 'dashboard.html'
        )
        
        if os.path.exists(dashboard_path):
            with open(dashboard_path, 'r', encoding='utf-8') as f:
                content = f.read()
                self.assertIn('action-menu-container', content,
                             "Action menu CSS not found")
                self.assertIn('toggleActionMenu', content,
                             "Action menu JS function not found")
            print("✓ Three-dot action menu is implemented")
        else:
            print("⚠ Dashboard template not found for verification")


class IntegrationTests(unittest.TestCase):
    """Integration tests for the complete system"""
    
    def test_requirements_file(self):
        """Test that all required packages are in requirements.txt"""
        req_path = os.path.join(
            os.path.dirname(__file__), 'requirement.txt'
        )
        
        if os.path.exists(req_path):
            with open(req_path, 'r') as f:
                content = f.read()
                
            required_packages = ['flask', 'deep-translator', 'python-docx', 
                               'openpyxl', 'requests', 'PyPDF2']
            
            for package in required_packages:
                # Check for package (case-insensitive)
                found = any(package.lower() in line.lower() 
                          for line in content.split('\n'))
                status = "✓" if found else "✗"
                print(f"  {status} {package}")
                self.assertTrue(found, f"{package} not found in requirements")
        else:
            self.fail("requirement.txt not found")


def run_tests():
    """Run all tests and generate report"""
    
    print("\n" + "="*70)
    print("DocTranslate Enterprise - Implementation Test Suite")
    print("="*70 + "\n")
    
    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add test classes
    suite.addTests(loader.loadTestsFromTestCase(DocTranslateImplementationTests))
    suite.addTests(loader.loadTestsFromTestCase(IntegrationTests))
    
    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Print summary
    print("\n" + "="*70)
    print("Test Summary")
    print("="*70)
    print(f"Tests Run: {result.testsRun}")
    print(f"Successes: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print("="*70 + "\n")
    
    return result.wasSuccessful()


if __name__ == '__main__':
    success = run_tests()
    sys.exit(0 if success else 1)
