# Configuration file for the Sphinx documentation builder.

# -- Project information

project = 'Yaetos'
copyright = '2018, Arthur Prevot'
author = 'Arthur Prevot'

release = '0.9'
version = '0.9.12'

# -- General configuration

extensions = [
    'sphinx.ext.duration',
    'sphinx.ext.doctest',
    'sphinx.ext.autodoc',
    'sphinx.ext.autosummary',
    'sphinx.ext.intersphinx',
]

intersphinx_mapping = {
    'python': ('https://docs.python.org/3/', None),
    'sphinx': ('https://www.sphinx-doc.org/en/master/', None),
}
intersphinx_disabled_domains = ['std']

templates_path = ['_templates']

# -- Options for HTML output

html_theme = 'alabaster' #default: 'sphinx_rtd_theme'
html_theme_options = {
    'show_powered_by': False,
    'github_user': 'arthurprevot',
    'github_repo': 'yaetos',
    'github_banner': True,
    'show_related': False,
    'note_bg': '#FFF59C'
}

# -- Options for EPUB output
epub_show_urls = 'footnote'
