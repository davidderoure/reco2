FROM python:3.14-alpine

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY proto ./proto
RUN mkdir -p generated && \
    python -m grpc_tools.protoc \
      -Iproto \
      --python_out=generated \
      --grpc_python_out=generated \
      --pyi_out=generated \
      proto/recommender.proto

COPY . .

EXPOSE 50051

CMD ["python", "server.py"]
