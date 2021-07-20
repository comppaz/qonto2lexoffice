# qonto2lexoffice

A simple serverless project containing an AWS lambda function that sends you a weekly email with a CSV of your Qonto transactions that is optimized for LexOffice.

Inspired by:
https://dev.classmethod.jp/articles/query-qonto-api-and-email-with-lambda/

Credits to:
https://dev.classmethod.jp/author/ito-mai/

## Requirements

### Setup SES

Configure a sender (e.g. *sender@example.com*) in SES and verify that e-mail address according the instructions.

### Setup secrets

Five parameters are required in the AWS parameter storage [https://eu-central-1.console.aws.amazon.com/systems-manager/parameters/](AWS parameter storage) in _eu-central-1_

```
qonto2lexoffice-sender=<sender@example.com>
qonto2lexoffice-recipient=<recipient@example.com>

qonto-api-key=<A212e...>
qonto-slug=<your-company-name-23213>
qonto-iban=<DE232133213...>

```
