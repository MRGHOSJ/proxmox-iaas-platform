FROM hashicorp/packer:1.10

# Install dependencies if needed (e.g., git, ansible)
# apk add --no-cache git ansible

WORKDIR /app
COPY . .

ENTRYPOINT ["packer"]