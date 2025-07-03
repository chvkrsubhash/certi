from pymongo import MongoClient

class MongoDB:
    def __init__(self, uri):
        self.client = MongoClient(uri)
        self.db = self.client['exam_portal']
        self.users = self.db['users']
        self.exams = self.db['exams']
        self.schedules = self.db['schedules']
        self.questions = self.db['questions']
        self.results = self.db['results']
        self.coupons = self.db['coupons']

    def get_db(self):
        return self.db