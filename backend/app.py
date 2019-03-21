import json

from bson import json_util, ObjectId
from flask import Flask, jsonify
from flask_cors import CORS
from flask_restful import Api, Resource
from pymongo import MongoClient, DESCENDING

# 生成Flask App实例
app = Flask(__name__)

# 生成MongoDB实例
mongo = MongoClient(host='192.168.99.100')
db = mongo['crawlab_test']
col = db['results']

# 生成API实例
api = Api(app)

# 支持CORS跨域
CORS(app, supports_credentials=True)


class ListApi(Resource):
    def get(self):
        # 查询
        items = col.find({'content': {'$exists': True}}).sort('_id', DESCENDING).limit(40)

        data = []
        for item in items:
            # 将pymongo object转化为python object
            _item = json.loads(json_util.dumps(item))

            data.append({
                '_id': _item['_id']['$oid'],
                'title': _item['title'],
                'source': _item['source'],
                'ts': item['_id'].generation_time.strftime('%Y-%m-%d %H:%M:%S')
            })

        return data


class DetailApi(Resource):
    def get(self, id):
        item = col.find_one({'_id': ObjectId(id)})

        # 将pymongo object转化为python object
        _item = json.loads(json_util.dumps(item))

        return {
            '_id': _item['_id']['$oid'],
            'title': _item['title'],
            'source': _item['source'],
            'ts': item['_id'].generation_time.strftime('%Y-%m-%d %H:%M:%S'),
            'content': _item['content']
        }


api.add_resource(ListApi, '/results')
api.add_resource(DetailApi, '/results/<string:id>')

if __name__ == '__main__':
    app.run()
