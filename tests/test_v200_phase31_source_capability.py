
def test_capability_enum_values(tmp_path):
    from app.sources.capability import SourceCapability
    assert SourceCapability.DIRECT_RESOURCE == 'direct_resource'
    assert SourceCapability.WEBPAGE_DISCOVERY == 'webpage_discovery'
    assert SourceCapability.HTML_LINK_DISCOVERY == 'html_link_discovery'
    assert SourceCapability.MEDIA_ELEMENT_DISCOVERY == 'media_element_discovery'
    assert SourceCapability.RESOURCE_PROBING == 'resource_probing'
    assert SourceCapability.DOWNLOAD == 'download'
    assert SourceCapability.UNSUPPORTED == 'unsupported'

def test_source_type_enum_values(tmp_path):
    from app.sources.capability import SourceType, SourceCapability
    assert SourceType.DIRECT == 'direct'
    assert SourceType.WEBPAGE == 'webpage'
    assert SourceType.GENERIC == 'generic'
    assert SourceType.PROTECTED == 'protected'
    assert SourceType.UNKNOWN == 'unknown'

def test_capability_is_enum_and_str(tmp_path):
    from enum import Enum
    from app.sources.capability import SourceCapability
    assert issubclass(SourceCapability, Enum)
    assert issubclass(SourceCapability, str)

def test_descriptor_creation(tmp_path):
    from app.sources.capability import SourceCapability, SourceType, SourceCapabilities
    caps = SourceCapabilities(source_type=SourceType.DIRECT, capabilities=frozenset({SourceCapability.DOWNLOAD}))
    assert caps.source_type == SourceType.DIRECT
    assert SourceCapability.DOWNLOAD in caps.capabilities

def test_descriptor_immutable(tmp_path):
    from app.sources.capability import SourceCapability, SourceType, SourceCapabilities
    caps = SourceCapabilities(source_type=SourceType.DIRECT, capabilities=frozenset({SourceCapability.DOWNLOAD}))
    try:
        caps.source_type = SourceType.WEBPAGE
        assert False, 'Should have raised'
    except AttributeError:
        pass
    try:
        caps.capabilities.add(SourceCapability.DIRECT_RESOURCE)
        assert False, 'Should have raised'
    except AttributeError:
        pass

def test_descriptor_supports(tmp_path):
    from app.sources.capability import SourceCapability, SourceType, SourceCapabilities
    caps = SourceCapabilities(source_type=SourceType.DIRECT, capabilities=frozenset({SourceCapability.DOWNLOAD, SourceCapability.RESOURCE_PROBING}))
    assert caps.supports(SourceCapability.DOWNLOAD) is True
    assert caps.supports(SourceCapability.DIRECT_RESOURCE) is False

def test_descriptor_empty_capability_set(tmp_path):
    from app.sources.capability import SourceCapability, SourceType, SourceCapabilities
    caps = SourceCapabilities(source_type=SourceType.UNKNOWN, capabilities=frozenset())
    assert caps.supports(SourceCapability.DOWNLOAD) is False

def test_descriptor_multiple_capabilities(tmp_path):
    from app.sources.capability import SourceCapability, SourceType, SourceCapabilities
    caps = SourceCapabilities(source_type=SourceType.WEBPAGE, capabilities=frozenset({SourceCapability.WEBPAGE_DISCOVERY, SourceCapability.HTML_LINK_DISCOVERY, SourceCapability.RESOURCE_PROBING}))
    assert caps.supports(SourceCapability.WEBPAGE_DISCOVERY)
    assert caps.supports(SourceCapability.HTML_LINK_DISCOVERY)
    assert caps.supports(SourceCapability.RESOURCE_PROBING)
    assert not caps.supports(SourceCapability.DOWNLOAD)

def test_direct_adapter_source_type(tmp_path):
    from app.sources.direct import DirectDownloadAdapter
    from app.sources.capability import SourceType, SourceCapability
    adapter = DirectDownloadAdapter()
    assert adapter.source_type == SourceType.DIRECT

def test_direct_adapter_capabilities(tmp_path):
    from app.sources.direct import DirectDownloadAdapter
    from app.sources.capability import SourceType, SourceCapability, SourceCapability, SourceCapabilities
    adapter = DirectDownloadAdapter()
    caps = adapter.capabilities
    assert caps.source_type == SourceType.DIRECT
    assert caps.supports(SourceCapability.DIRECT_RESOURCE)
    assert caps.supports(SourceCapability.RESOURCE_PROBING)
    assert caps.supports(SourceCapability.DOWNLOAD)

def test_direct_adapter_no_webpage_discovery(tmp_path):
    from app.sources.direct import DirectDownloadAdapter
    from app.sources.capability import SourceCapability
    adapter = DirectDownloadAdapter()
    assert not adapter.capabilities.supports(SourceCapability.WEBPAGE_DISCOVERY)
    assert not adapter.capabilities.supports(SourceCapability.HTML_LINK_DISCOVERY)

def test_generic_adapter_source_type(tmp_path):
    from app.sources.generic import GenericSourceAdapter
    from app.sources.capability import SourceType, SourceCapability
    adapter = GenericSourceAdapter()
    assert adapter.source_type == SourceType.WEBPAGE

def test_generic_adapter_capabilities(tmp_path):
    from app.sources.generic import GenericSourceAdapter
    from app.sources.capability import SourceType, SourceCapability, SourceCapability, SourceCapabilities
    adapter = GenericSourceAdapter()
    caps = adapter.capabilities
    assert caps.source_type == SourceType.WEBPAGE
    assert caps.supports(SourceCapability.WEBPAGE_DISCOVERY)
    assert caps.supports(SourceCapability.HTML_LINK_DISCOVERY)
    assert caps.supports(SourceCapability.RESOURCE_PROBING)

def test_generic_adapter_no_direct_resource(tmp_path):
    from app.sources.generic import GenericSourceAdapter
    from app.sources.capability import SourceCapability
    adapter = GenericSourceAdapter()
    assert not adapter.capabilities.supports(SourceCapability.DIRECT_RESOURCE)
    assert not adapter.capabilities.supports(SourceCapability.DOWNLOAD)

def test_protected_adapter_source_type(tmp_path):
    from app.sources.protected import ProtectedSourceAdapter
    from app.sources.capability import SourceType, SourceCapability
    adapter = ProtectedSourceAdapter()
    assert adapter.source_type == SourceType.PROTECTED

def test_protected_adapter_capabilities(tmp_path):
    from app.sources.protected import ProtectedSourceAdapter
    from app.sources.capability import SourceType, SourceCapability, SourceCapability, SourceCapabilities
    adapter = ProtectedSourceAdapter()
    caps = adapter.capabilities
    assert caps.source_type == SourceType.PROTECTED
    assert caps.supports(SourceCapability.UNSUPPORTED)

def test_protected_adapter_no_download(tmp_path):
    from app.sources.protected import ProtectedSourceAdapter
    from app.sources.capability import SourceCapability
    adapter = ProtectedSourceAdapter()
    assert not adapter.capabilities.supports(SourceCapability.DOWNLOAD)
    assert not adapter.capabilities.supports(SourceCapability.DIRECT_RESOURCE)
    assert not adapter.capabilities.supports(SourceCapability.RESOURCE_PROBING)

def test_base_adapter_default_capabilities(tmp_path):
    from app.sources.base import BaseSourceAdapter
    from app.sources.capability import SourceCapability, SourceType, SourceCapabilities
    class _Stub(BaseSourceAdapter):
        @property
        def name(self): return 'Stub'
        def can_handle(self, url): return True
        async def analyze(self, url):
            from app.core.models import AnalysisResult
            return AnalysisResult(title='t', source='Stub', files=[], status='ready')
    stub = _Stub()
    assert stub.source_type == SourceType.UNKNOWN
    assert stub.capabilities.source_type == SourceType.UNKNOWN
    assert stub.capabilities.supports(SourceCapability.UNSUPPORTED)
    assert not stub.capabilities.supports(SourceCapability.DOWNLOAD)

def test_detector_classify_direct(tmp_path):
    from app.services.source_detector import SourceDetector
    detector = SourceDetector()
    caps = detector.classify('https://example.com/file.zip')
    assert caps.source_type == 'direct'
    assert caps.supports('direct_resource')
    assert caps.supports('download')

def test_detector_classify_webpage(tmp_path):
    from app.services.source_detector import SourceDetector
    detector = SourceDetector()
    caps = detector.classify('https://example.com/page')
    assert caps.source_type == 'webpage'
    assert caps.supports('webpage_discovery')
    assert caps.supports('html_link_discovery')
    assert not caps.supports('download')

def test_detector_classify_protected(tmp_path):
    from app.services.source_detector import SourceDetector
    detector = SourceDetector()
    caps = detector.classify('https://example.com/login')
    assert caps.source_type == 'protected'
    assert caps.supports('unsupported')
    assert not caps.supports('download')

def test_detector_classify_unknown(tmp_path):
    from app.services.source_detector import SourceDetector
    from app.sources.capability import SourceType, SourceCapability
    detector = SourceDetector()
    caps = detector.classify('not-a-url')
    assert caps.source_type == SourceType.UNKNOWN
    assert caps.supports('unsupported')
    assert not caps.supports('download')

def test_detector_classify_consistent(tmp_path):
    from app.services.source_detector import SourceDetector
    detector = SourceDetector()
    caps1 = detector.classify('https://example.com/file.zip')
    caps2 = detector.classify('https://example.com/file.zip')
    assert caps1.source_type == caps2.source_type
    assert caps1.capabilities == caps2.capabilities

def test_detector_classify_no_adapter(tmp_path):
    from app.services.source_detector import SourceDetector
    from app.sources.capability import SourceType, SourceCapability
    detector = SourceDetector()
    caps = detector.classify('unknown-source://test')
    assert caps.source_type == SourceType.UNKNOWN
    assert caps.supports(SourceCapability.UNSUPPORTED)