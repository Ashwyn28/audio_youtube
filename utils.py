def parse_query_response(data):
    return [
        {
            "id": item["id"]["videoId"],
            "title": item["snippet"]["title"],
            "timestamp": item["snippet"]["publishTime"],
        }
        for item in data["items"]
    ]


def parse_channel_response(data):
    item = data["items"][0]
    return {"id": item["id"]["channelId"], "name": item["snippet"]["title"]}


def parse_channels_response(channels_data):
    return sorted(channels_data, key=lambda x: x["timestamp"], reverse=True)
