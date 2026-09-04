resource "aws_lambda_function" "worker" {
  function_name = "sample-worker"
  handler       = "index.handler"
  runtime       = "nodejs20.x"
  role          = aws_iam_role.lambda.arn
}
