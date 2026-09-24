FROM scratch
COPY hello.txt /hello.txt
# Named context staged by setup-commands in self-ci.yml.
COPY --from=staged note.txt /note.txt
