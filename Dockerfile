FROM public.ecr.aws/lambda/python:3.12

RUN pip install --no-cache-dir \
    "torch==2.14.0" --index-url https://download.pytorch.org/whl/cpu

RUN pip install --no-cache-dir --no-deps "cordon==1.1.1"

COPY requirements/triage.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY src/beacon/ ${LAMBDA_TASK_ROOT}/beacon/

CMD ["beacon.handler.handler"]
