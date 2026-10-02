FROM golang:1.23-alpine AS build
WORKDIR /src
COPY go.mod go.sum *.go ./
COPY templates ./templates
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /share .

FROM alpine:3.20
# mailcap provides /etc/mime.types so uncommon extensions get a proper Content-Type.
RUN apk add --no-cache mailcap
COPY --from=build /share /usr/local/bin/share
ARG SHARE_UID=1000
ARG SHARE_GID=1000
USER ${SHARE_UID}:${SHARE_GID}
EXPOSE 8080
ENTRYPOINT ["share"]
