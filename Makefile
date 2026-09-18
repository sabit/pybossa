.PHONY: test
test:
	uv run nosetests test/

.PHONY: release
release:
	npx standard-version . && git push --follow-tags origin master
