from pymongo import MongoClient
from werkzeug.security import generate_password_hash

# MongoDB Atlas connection string
MONGO_URI = "mongodb+srv://certuser:nuRSt7en6HufxSbQ@cluster0.axqijq3.mongodb.net/exam_portal?retryWrites=true&w=majority"


try:
    client = MongoClient(MONGO_URI)
    db = client['exam_portal']
    print("Connected to MongoDB Atlas!")
    print("Databases:", client.list_database_names())
    print("Collections in exam_portal:", db.list_collection_names())
    print("Exams:", list(db.exams.find()))
except Exception as e:
    print("Connection failed:", str(e))