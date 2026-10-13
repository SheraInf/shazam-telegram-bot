import unittest
import sqlite3
from store import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')

    def tearDown(self):
        self.store.db.close()

    def test_first_referral_only_and_unique_users(self):
        self.store.ref('first','First')
        self.store.ref('second','Second')
        self.store.touch(1,'private',1,'first')
        self.store.touch(1,'private',1,'second')
        self.store.touch(-100,'supergroup',1)
        rows = {code:count for code,name,active,count in self.store.refs()}
        self.assertEqual(rows,{'first':1,'second':0})
        self.assertEqual(self.store.stats(),(1,1))

    def test_unknown_and_disabled_referrals(self):
        self.store.ref('old','Old')
        self.store.disable_ref('old')
        self.store.touch(1,'private',1,'old')
        self.store.touch(2,'private',2,'unknown')
        self.assertEqual(self.store.refs()[0][3],0)

    def test_settings_cache_and_blocked_recipients(self):
        self.store.set('welcome','Привет')
        self.assertEqual(self.store.get('welcome'),'Привет')
        self.store.save_media('audio:abcdefghijk',{'file_id':'cached'})
        self.assertEqual(self.store.media('audio:abcdefghijk')['file_id'],'cached')
        self.store.touch(1,'private',1)
        self.store.blocked(1)
        self.assertEqual(self.store.recipients(),[])
        self.store.touch(1,'private',1)
        self.assertEqual(self.store.recipients(),[1])

    def test_invalid_duplicate_referral(self):
        with self.assertRaises(ValueError):
            self.store.ref('invalid code','name')
        self.store.ref('safe','Name')
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.ref('safe','Duplicate')
