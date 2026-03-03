# -*- coding: utf-8 -*-
import sys
import json
from app import app, export_excel
from flask import Request
import os

payload = {
    'results': [
        {'name': 'Aishat Musa', 'class': 'SS 1Q', 'score': '8'},
        {'name': 'Bola Ahmed', 'class': 'SS 1Q', 'score': '6'},
        {'name': 'Chidi Obi', 'class': 'SS 1Q', 'score': '9'},
    ],
    'assessmentType': '1st CA',
    'subjectType': 'Mathematics',
    'term': '1st Term',
    'subjectMode': 'general',
    'classList': ['SS 1Q'],
    'existingRecords': None
}

with app.test_request_context('/export-excel', method='POST', json=payload):
    response = export_excel()
    print('1st Term Export Data:', response[0].json)

payload2 = {
    'results': [
        {'name': 'Aishat Musa', 'class': 'SS 1Q', 'score': '7'},
        {'name': 'Bola Ahmed', 'class': 'SS 1Q', 'score': '8'},
    ],
    'assessmentType': '2nd CA',
    'subjectType': 'Mathematics',
    'term': '1st Term',
    'subjectMode': 'general',
    'classList': ['SS 1Q'],
    'existingRecords': [
        {'Name': 'Aishat Musa', 'Class': 'SS 1Q', '1st CA': 8},
        {'Name': 'Bola Ahmed', 'Class': 'SS 1Q', '1st CA': 6},
        {'Name': 'Chidi Obi', 'Class': 'SS 1Q', '1st CA': 9}
    ]
}

with app.test_request_context('/export-excel', method='POST', json=payload2):
    response2 = export_excel()
    print('2nd CA Export Data:', response2[0].json)
    
print("Excels generated in directory.")
