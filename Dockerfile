FROM python:3.8

ADD code /code
RUN pip install -r /code/requirements.txt

WORKDIR /code
ENV PYTHONPATH '/code/'

CMD ["python" , "-u", "/code/collector.py"]