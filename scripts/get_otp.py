#!/usr/bin/env python3
"""Brute-force the latest MFA OTP for a given email by reading code_hash from MongoDB."""
import os, sys, hmac, hashlib
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv('/app/backend/.env')
email = sys.argv[1] if len(sys.argv) > 1 else 'miguelingv@gmail.com'
try:
    client = MongoClient(os.environ['MONGO_URL'])
    db = client[os.environ['DB_NAME']]
    rec = db.auth_mfa_challenges.find_one({'email': email}, sort=[('created_at', -1)])
    if not rec:
        print('NO_CHALLENGE', file=sys.stderr); sys.exit(1)
    secret = os.environ['JWT_SECRET'].encode()
    target = rec['code_hash']
    for i in range(1000000):
        c = f'{i:06d}'
        if hmac.new(secret, c.encode(), hashlib.sha256).hexdigest() == target:
            print(c); sys.exit(0)
    print('NOT_FOUND', file=sys.stderr); sys.exit(2)
except Exception as e:
    print(f'ERR: {e}', file=sys.stderr); sys.exit(3)
