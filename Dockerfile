FROM public.ecr.aws/lambda/python:3.11

RUN yum install -y git && yum clean all

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r requirements.txt

COPY . ${LAMBDA_TASK_ROOT}/

CMD ["lambda_handler.handler"]
